from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.governance_contracts import AppliesTo
from ads_booster.knowledge.operation_enums import SkillOrigin
from ads_booster.knowledge.skill_contracts import (
    SkillCatalogEntry,
    SkillRecord,
    SkillReference,
)
from ads_booster.knowledge.skill_source_currentness import skill_sources_are_current

if TYPE_CHECKING:
    from ads_booster.agent.service.skills import MarketingSkill
    from ads_booster.knowledge.evidence_contracts import EvidenceRef
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.repository_types import StoredSkill
    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.transport.json_types import JsonObject

_BUILTIN_TIMESTAMP = datetime(1970, 1, 1, tzinfo=UTC)
type OverrideStatus = Literal["current", "base_release_mismatch", "source_stale"]


class SkillReferenceLike(Protocol):
    @property
    def skill_id(self) -> str: ...

    @property
    def revision_id(self) -> str: ...

    @property
    def content_sha256(self) -> str: ...

    @property
    def origin(self) -> SkillOrigin: ...

    @property
    def protected(self) -> bool: ...

    @property
    def source_refs(self) -> tuple[EvidenceRef, ...]: ...


@dataclass(frozen=True, slots=True)
class SkillRead:
    record: SkillRecord
    markdown: str
    display_pending: bool = False
    effective: bool = True
    override_status: OverrideStatus | None = None


def builtin_skill_records() -> tuple[SkillRecord, ...]:
    """Project every current built-in marketing procedure into a protected record."""
    from ads_booster.agent.service.skills import SKILLS  # noqa: PLC0415 - lazy source catalog

    return tuple(_builtin_record(skill) for skill in SKILLS)


def known_capability_ids() -> frozenset[str]:
    """Return IDs known to either the knowledge tools or the built-in skill catalog."""
    from ads_booster.knowledge.tool_contracts import (  # noqa: PLC0415 - avoids tool cycle
        KnowledgeToolName,
    )

    return frozenset(
        tuple(item.value for item in KnowledgeToolName)
        + tuple(
            capability
            for record in builtin_skill_records()
            for capability in record.required_capability_ids
        )
    )


def render_skill_markdown(record: SkillRecord) -> str:
    """Render a reviewable SKILL.md view from the committed record."""
    pitfalls = "\n".join(f"- {item}" for item in record.pitfalls) or "- None recorded."
    verification = "\n".join(f"- {item}" for item in record.verification) or "- None recorded."
    capabilities = ", ".join(record.required_capability_ids) or "none"
    return (
        "---\n"
        f"skill_id: {record.skill_id}\n"
        f"revision_id: {record.version}\n"
        f"digest: {record.digest}\n"
        f"origin: {record.origin.value}\n"
        f"protected: {str(record.protected).lower()}\n"
        f"required_capabilities: {capabilities}\n"
        "---\n\n"
        f"# {record.description}\n\n"
        f"{record.procedure}\n\n"
        "## Pitfalls\n\n"
        f"{pitfalls}\n\n"
        "## Verification\n\n"
        f"{verification}\n"
    )


class KnowledgeSkills:
    """Resolve committed learned revisions against the live built-in catalog."""

    def __init__(self, repository: SqliteKnowledgeRepository) -> None:
        """Bind the existing repository as the catalog source."""
        self._repository: SqliteKnowledgeRepository = repository

    def list(
        self,
        actor: ActorContext,
        applicability: AppliesTo | None = None,
        *,
        include_protected: bool = True,
    ) -> tuple[SkillCatalogEntry, ...]:
        builtins = builtin_skill_records()
        builtin_ids = {record.skill_id for record in builtins}
        selected = [self.get(actor, record.skill_id) for record in builtins]
        selected.extend(
            self.get(actor, skill_id)
            for skill_id in self._repository.skill_ids(actor)
            if skill_id not in builtin_ids
        )
        return tuple(
            _catalog_entry(item)
            for item in selected
            if item is not None
            and (include_protected or not item.record.protected)
            and _applies(item.record.applicability, applicability)
        )

    def get(  # noqa: PLR0911 - effective and fallback variants terminate independently
        self,
        actor: ActorContext,
        skill_id: str,
        revision_id: str | None = None,
    ) -> SkillRead | None:
        builtin = next(
            (record for record in builtin_skill_records() if record.skill_id == skill_id),
            None,
        )
        stored = self._repository.read_skill(actor, skill_id, revision_id)
        stored_is_current = stored is not None and skill_sources_are_current(
            self._repository,
            actor,
            stored.record.skill_id,
            stored.record.source_refs,
        )
        if revision_id is not None:
            if stored is not None and stored_is_current:
                return SkillRead(
                    record=stored.record,
                    markdown=stored.body.decode(),
                    display_pending=stored.display_pending,
                    effective=_stored_is_effective(stored.record, builtin),
                    override_status=_override_status(stored.record, builtin),
                )
            if builtin is not None and builtin.version == revision_id:
                return SkillRead(record=builtin, markdown=render_skill_markdown(builtin))
            return None
        if builtin is None:
            if (
                stored is None
                or not stored_is_current
                or stored.record.origin is not SkillOrigin.AGENT_CREATED
            ):
                return None
            return SkillRead(
                record=stored.record,
                markdown=stored.body.decode(),
                display_pending=stored.display_pending,
            )
        if stored_is_current and _stored_is_effective(
            None if stored is None else stored.record,
            builtin,
        ):
            if stored is None:
                return None
            return SkillRead(
                record=stored.record,
                markdown=stored.body.decode(),
                display_pending=stored.display_pending,
                override_status="current",
            )
        return SkillRead(
            record=builtin,
            markdown=render_skill_markdown(builtin),
            override_status=(_fallback_status(stored, stored_is_current)),
        )

    def reference(self, actor: ActorContext, skill_id: str) -> SkillReference | None:
        selected = self.get(actor, skill_id)
        return None if selected is None else _reference(selected.record)

    def is_current(self, actor: ActorContext, reference: SkillReferenceLike) -> bool:
        current = self.reference(actor, reference.skill_id)
        return current is not None and (
            current.revision_id,
            current.content_sha256,
            current.origin,
            current.protected,
            current.source_refs,
        ) == (
            reference.revision_id,
            reference.content_sha256,
            reference.origin,
            reference.protected,
            reference.source_refs,
        )


def _builtin_record(skill: MarketingSkill) -> SkillRecord:
    stable: JsonObject = {
        "skill_id": skill.skill_id,
        "version": skill.version,
        "description": skill.purpose,
        "procedure": skill.procedure,
        "pitfalls": [],
        "verification": list(skill.success_criteria),
        "required_capability_ids": list(skill.required_capabilities),
        "origin": SkillOrigin.BUILTIN.value,
        "protected": True,
        "base_builtin_digest": None,
        "source_refs": [],
        "applicability": {},
        "created_by": "system.builtin-catalog",
    }
    return SkillRecord(
        schema="knowledge.skill-record.v1",
        skill_id=skill.skill_id,
        version=skill.version,
        description=skill.purpose,
        procedure=skill.procedure,
        verification=skill.success_criteria,
        required_capability_ids=skill.required_capabilities,
        origin=SkillOrigin.BUILTIN,
        protected=True,
        digest=contract_sha256(stable),
        applicability=AppliesTo(),
        created_by="system.builtin-catalog",
        created_at=_BUILTIN_TIMESTAMP,
        updated_at=_BUILTIN_TIMESTAMP,
    )


def _reference(record: SkillRecord) -> SkillReference:
    return SkillReference(
        skill_id=record.skill_id,
        revision_id=record.version,
        content_sha256=record.digest,
        origin=record.origin,
        protected=record.protected,
        source_refs=record.source_refs,
    )


def _catalog_entry(selected: SkillRead) -> SkillCatalogEntry:
    return SkillCatalogEntry(
        reference=_reference(selected.record),
        description=selected.record.description,
        required_capability_ids=selected.record.required_capability_ids,
        applicability=selected.record.applicability,
        override_status=selected.override_status,
    )


def _stored_is_effective(record: SkillRecord | None, builtin: SkillRecord | None) -> bool:
    if record is None:
        return False
    if builtin is None:
        return record.origin is SkillOrigin.AGENT_CREATED
    return (
        record.origin is SkillOrigin.BUILTIN_OVERRIDE
        and record.base_builtin_digest == builtin.digest
    )


def _override_status(
    record: SkillRecord,
    builtin: SkillRecord | None,
) -> OverrideStatus | None:
    if record.origin is not SkillOrigin.BUILTIN_OVERRIDE:
        return None
    if builtin is not None and record.base_builtin_digest == builtin.digest:
        return "current"
    return "base_release_mismatch"


def _fallback_status(
    stored: StoredSkill | None,
    stored_is_current: bool,
) -> OverrideStatus | None:
    if stored is None or stored.record.origin is not SkillOrigin.BUILTIN_OVERRIDE:
        return None
    return "base_release_mismatch" if stored_is_current else "source_stale"


def _applies(candidate: AppliesTo, requested: AppliesTo | None) -> bool:
    if requested is None:
        return True
    if candidate.task_ref is not None and candidate.task_ref != requested.task_ref:
        return False
    if (
        requested.subject_key is not None
        and candidate.subject_key is not None
        and candidate.subject_key != requested.subject_key
    ):
        return False
    if candidate.action_kinds and not set(candidate.action_kinds).intersection(
        requested.action_kinds
    ):
        return False
    return not candidate.product_refs or bool(
        set(candidate.product_refs).intersection(requested.product_refs)
    )


__all__ = [
    "KnowledgeSkills",
    "SkillRead",
    "SkillReferenceLike",
    "builtin_skill_records",
    "known_capability_ids",
    "render_skill_markdown",
]
