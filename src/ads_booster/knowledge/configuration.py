from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from ads_booster.contracts.agent_run import BoundedId  # noqa: TC001 -- Pydantic runtime type
from ads_booster.contracts.models import ContractModel
from ads_booster.knowledge.change_publication import ChangeGroup, ChangePublisher, MemoryPublication
from ads_booster.knowledge.contract_types import GrantCapability, MemoryKind, ScopeKind
from ads_booster.knowledge.memory import MemorySnapshot
from ads_booster.knowledge.memory_contracts import MemoryDocument, MemoryRevision
from ads_booster.knowledge.operation_contracts import MemoryOperation
from ads_booster.knowledge.operation_enums import MemoryOperationKind
from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.scope_contracts import AccessScope, ActorContext, ScopeGrant

if TYPE_CHECKING:
    from collections.abc import Mapping

KNOWLEDGE_ROOT_ENV = "TRACE_MARKETING_KNOWLEDGE_ROOT"
CONTROL_ROOT_ENV = "TRACE_MARKETING_KNOWLEDGE_CONTROL_ROOT"
POLICY_ENV = "TRACE_MARKETING_KNOWLEDGE_POLICY"
IDENTITY_FILE = "identity.json"


class LocalKnowledgeIdentity(ContractModel):
    schema_version: Literal["trace.knowledge-local-identity.v1"] = Field(alias="schema")
    actor_id: Annotated[str, Field(min_length=1, max_length=160)]
    workspace_id: Annotated[str, Field(min_length=1, max_length=160)]
    member_id: Annotated[str, Field(min_length=1, max_length=160)]
    session_id: Annotated[str, Field(min_length=1, max_length=160)]


def _missing_channel(value: str | None) -> bool:
    return value is None


class LocalKnowledgePolicy(ContractModel):
    schema_version: Literal["trace.knowledge-local-policy.v1"] = Field(alias="schema")
    workspace_id: Annotated[str, Field(min_length=1, max_length=160)]
    policy_epoch: Annotated[int, Field(ge=1)]
    capabilities: tuple[GrantCapability, ...]
    brand_voice_brand_ids: tuple[str, ...] = ()
    channel_id: BoundedId | None = Field(default=None, exclude_if=_missing_channel)


@dataclass(frozen=True, slots=True)
class KnowledgeSettings:
    root: Path | None = None
    control_root: Path | None = None
    policy_path: Path | None = None

    @property
    def enabled(self) -> bool:
        return self.root is not None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> KnowledgeSettings:
        values = tuple(env.get(key, "").strip() for key in _CONFIG_KEYS)
        if not any(values):
            return cls()
        if not all(values):
            raise ValueError("knowledge_configuration_partial")
        root, control, policy = (Path(value) for value in values)
        for path in (root, control, policy):
            if not path.is_absolute():
                raise ValueError("knowledge_configuration_requires_absolute_paths")
        return cls(root=root, control_root=control, policy_path=policy)

    def require_enabled(self) -> tuple[Path, Path, Path]:
        if self.root is None or self.control_root is None or self.policy_path is None:
            raise ValueError("knowledge_disabled")
        return self.root, self.control_root, self.policy_path


def initialize_local_configuration(
    root: Path,
    control_root: Path,
    policy_path: Path,
    *,
    workspace_id: str,
) -> tuple[LocalKnowledgeIdentity, LocalKnowledgePolicy]:
    for path in (root, control_root, policy_path):
        if not path.is_absolute():
            raise ValueError("knowledge_configuration_requires_absolute_paths")
    _private_directory(root, create=True)
    _private_directory(control_root, create=True)
    identity = LocalKnowledgeIdentity(
        schema="trace.knowledge-local-identity.v1",
        actor_id=f"local-owner-{os.getuid()}",
        workspace_id=workspace_id,
        member_id=f"local-member-{os.getuid()}",
        session_id="local-admin-session",
    )
    policy = LocalKnowledgePolicy(
        schema="trace.knowledge-local-policy.v1",
        workspace_id=workspace_id,
        policy_epoch=1,
        capabilities=(
            GrantCapability.READ,
            GrantCapability.WRITE,
            GrantCapability.SCHEDULE,
            GrantCapability.SHARE,
            GrantCapability.PURGE,
        ),
        brand_voice_brand_ids=(),
    )
    _write_private_json(
        control_root / IDENTITY_FILE, identity.model_dump(mode="json", by_alias=True)
    )
    _write_private_json(policy_path, policy.model_dump(mode="json", by_alias=True))
    return identity, policy


def load_local_actor(settings: KnowledgeSettings, *, now: datetime | None = None) -> ActorContext:
    root, control_root, policy_path = settings.require_enabled()
    _private_directory(root)
    _private_directory(control_root)
    _private_file(policy_path)
    identity_path = control_root / IDENTITY_FILE
    _private_file(identity_path)
    identity = LocalKnowledgeIdentity.model_validate_json(identity_path.read_bytes())
    policy = LocalKnowledgePolicy.model_validate_json(policy_path.read_bytes())
    if identity.workspace_id != policy.workspace_id:
        raise ValueError("knowledge_identity_policy_workspace_mismatch")
    timestamp = now or datetime.now(UTC)
    scope = AccessScope(
        kind=ScopeKind.WORKSPACE if policy.channel_id is None else ScopeKind.CHANNEL,
        workspace_id=identity.workspace_id,
        channel_id=policy.channel_id,
    )
    channel_namespace = None if policy.channel_id is None else scope_key(scope)
    grant_suffix = "" if channel_namespace is None else f"-{channel_namespace}"
    session_id = (
        identity.session_id
        if channel_namespace is None
        else (
            "local-channel-session-"
            + sha256(f"{identity.session_id}:{channel_namespace}".encode()).hexdigest()[:40]
        )
    )
    grants = tuple(
        ScopeGrant(
            grant_id=f"local-{capability.value}-{policy.policy_epoch}{grant_suffix}",
            capability=capability,
            workspace_id=identity.workspace_id,
            scope=scope,
            policy_epoch=policy.policy_epoch,
            effective_at=datetime(1970, 1, 1, tzinfo=UTC),
        )
        for capability in policy.capabilities
        if capability is not GrantCapability.BRAND_VOICE_EDIT
    )
    grants += tuple(
        ScopeGrant(
            grant_id=(
                f"local-brand-voice-{brand_id}-{policy.policy_epoch}"
                if channel_namespace is None
                else "local-channel-brand-voice-"
                + sha256(
                    f"{brand_id}:{channel_namespace}:{policy.policy_epoch}".encode()
                ).hexdigest()[:40]
            ),
            capability=GrantCapability.BRAND_VOICE_EDIT,
            workspace_id=identity.workspace_id,
            scope=scope,
            brand_id=brand_id,
            policy_epoch=policy.policy_epoch,
            effective_at=datetime(1970, 1, 1, tzinfo=UTC),
        )
        for brand_id in policy.brand_voice_brand_ids
    )
    return ActorContext(
        actor_id=identity.actor_id,
        workspace_id=identity.workspace_id,
        member_id=identity.member_id,
        session_id=session_id,
        conversation_scope=scope,
        grants=grants,
        policy_epoch=policy.policy_epoch,
        authenticated_at=timestamp,
    )


def validate_settings(settings: KnowledgeSettings) -> None:
    _ = load_local_actor(settings)


def initialize_knowledge_store(settings: KnowledgeSettings) -> ActorContext:
    root, _, _ = settings.require_enabled()
    actor = load_local_actor(settings)
    repository = SqliteKnowledgeRepository(root)
    repository.register_actor(actor, MembershipRole.ADMIN)
    namespace = (
        f".{scope_key(actor.conversation_scope)}"
        if actor.conversation_scope.kind is ScopeKind.CHANNEL
        else ""
    )
    document_scope = actor.conversation_scope if namespace else None
    operation_id = f"operation.knowledge.init{namespace}"
    body = b""
    snapshots: list[MemorySnapshot] = []
    operations: list[MemoryOperation] = []
    for kind in (MemoryKind.TEAM, MemoryKind.CORE):
        document_id = f"memory.{kind.value}{namespace}"
        revision_id = f"{document_id}.initial"
        document = MemoryDocument(
            document_id=document_id,
            workspace_id=actor.workspace_id,
            kind=kind,
            timezone="UTC",
            head_revision_id=revision_id,
            scope=document_scope,
        )
        revision = MemoryRevision(
            document_id=document_id,
            revision_id=revision_id,
            previous_revision_id=None,
            body_sha256=sha256(body).hexdigest(),
            entry_ids=(),
            created_at=actor.authenticated_at,
        )
        snapshots.append(MemorySnapshot(document, revision, (), body))
        operations.append(
            MemoryOperation(
                operation_id=operation_id,
                kind=MemoryOperationKind.ADD,
                document_id=document_id,
                entry_id=f"{document_id}.empty",
                expected_revision_id="none",
                reason="Initialize an empty canonical memory document.",
                evidence_refs=("local.knowledge.init",),
            )
        )
    _ = ChangePublisher(repository).publish(
        actor=actor,
        group=ChangeGroup(
            operation_id=operation_id,
            memory_operations=tuple(operations),
        ),
        pages=None,
        memories=tuple(MemoryPublication(snapshot) for snapshot in snapshots),
        at=actor.authenticated_at,
    )
    return actor


def _private_directory(path: Path, *, create: bool = False) -> None:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("knowledge_root_owner_invalid")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("knowledge_root_permissions_invalid")


def _private_file(path: Path) -> None:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("knowledge_configuration_owner_invalid")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("knowledge_configuration_permissions_invalid")


def _write_private_json(path: Path, value: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    path.chmod(0o600)


_CONFIG_KEYS = (KNOWLEDGE_ROOT_ENV, CONTROL_ROOT_ENV, POLICY_ENV)


__all__ = [
    "CONTROL_ROOT_ENV",
    "IDENTITY_FILE",
    "KNOWLEDGE_ROOT_ENV",
    "POLICY_ENV",
    "KnowledgeSettings",
    "LocalKnowledgeIdentity",
    "LocalKnowledgePolicy",
    "initialize_knowledge_store",
    "initialize_local_configuration",
    "load_local_actor",
    "validate_settings",
]
