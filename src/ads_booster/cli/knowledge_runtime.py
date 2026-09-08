from __future__ import annotations

import json
import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from ads_booster.knowledge.configuration import KnowledgeSettings, load_local_actor
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.ingestion_sources import TrustedSourcePayload
from ads_booster.knowledge.maintenance import KnowledgeOwner
from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from ads_booster.knowledge.tool_contracts import TrustedInvocationContext
from ads_booster.knowledge.tools import ToolHost

if TYPE_CHECKING:
    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.knowledge.source_contracts import AttachmentCapability
    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class LocalAttachmentReader:
    paths: dict[int, Path]

    def read(
        self,
        actor: ActorContext,
        attachment: AttachmentCapability,
    ) -> TrustedSourcePayload:
        _ = actor
        path = self.paths.get(attachment.ordinal)
        if path is None or not path.is_absolute():
            raise ValueError("knowledge_attachment_capability_not_found")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("knowledge_attachment_owner_invalid")
        return TrustedSourcePayload(
            logical_source_ref=attachment.logical_source_ref,
            logical_revision_ref=attachment.logical_revision_ref,
            data=path.read_bytes(),
            mime_type=attachment.mime_type,
            sanitized_locator=path.name,
        )


@dataclass(slots=True)
class CliKnowledgeSession:
    settings: KnowledgeSettings
    attachment_paths: dict[int, Path] | None = None
    actor: ActorContext = field(init=False)
    repository: SqliteKnowledgeRepository = field(init=False)
    ingestion: KnowledgeIngestion = field(init=False)
    host: ToolHost = field(init=False)
    owner: KnowledgeOwner = field(init=False)

    def __post_init__(self) -> None:
        root, _, _ = self.settings.require_enabled()
        self.actor = load_local_actor(self.settings)
        self.owner = KnowledgeOwner(root, f"cli-{uuid4().hex}")
        with ExitStack() as initialization:
            _ = initialization.callback(self.owner.release)
            self.owner.acquire()
            self.repository = SqliteKnowledgeRepository(root)
            self.repository.register_actor(self.actor, MembershipRole.ADMIN)
            reader = (
                None
                if self.attachment_paths is None
                else LocalAttachmentReader(self.attachment_paths)
            )
            self.ingestion = KnowledgeIngestion(self.repository, attachment_reader=reader)
            self.host = ToolHost(self.repository, ingestion=self.ingestion)
            _ = initialization.pop_all()

    def trusted_context(
        self,
        *,
        task_id: str | None = None,
        brand_id: str | None = None,
        job_id: str | None = None,
    ) -> TrustedInvocationContext:
        invocation = uuid4().hex
        return TrustedInvocationContext(
            invocation_id=f"cli.{invocation}",
            actor=self.actor,
            run_binding_id=f"cli.binding.{invocation}",
            run_id=f"cli.run.{invocation}",
            task_id=task_id,
            job_id=job_id,
            brand_id=brand_id,
            capability_epoch=self.actor.policy_epoch,
            invoked_at=datetime.now(UTC),
        )

    def execute(
        self,
        name: str,
        arguments: JsonObject,
        *,
        task_id: str | None = None,
        brand_id: str | None = None,
    ) -> str:
        return self.host.execute(
            name,
            arguments,
            self.trusted_context(task_id=task_id, brand_id=brand_id),
        ).model_dump_json(indent=2)

    def close(self) -> None:
        self.owner.release()

    def __enter__(self) -> CliKnowledgeSession:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def settings_from_options(root: Path, control_root: Path, policy: Path) -> KnowledgeSettings:
    return KnowledgeSettings(root=root, control_root=control_root, policy_path=policy)


def read_json(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("knowledge_request_object_required")
    return cast("JsonObject", value)


__all__ = ["CliKnowledgeSession", "LocalAttachmentReader", "read_json", "settings_from_options"]
