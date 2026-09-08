"""Read only signed-event-bound Slack images through the existing Codex review owner."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Self

from pydantic import Field, TypeAdapter, model_validator

from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256
from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.agent_service.image_review import review_images
from ads_booster.marketing.agent_service.slack_image_files import (
    ReadResponse,
    SlackImageFiles,
    open_slack_image_request,
)
from ads_booster.marketing.tool_adapters.compatibility import DelegatedToolResult
from ads_booster.marketing.tool_adapters.descriptors import research_descriptor
from ads_booster.providers.codex_cli import CodexCli
from ads_booster.transport.json_types import JsonObject, JsonValue

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from datetime import datetime
    from pathlib import Path

    from ads_booster.contracts.tool_capability import ToolDescriptor

_FILE = re.compile(r"F[A-Z0-9]{1,79}")
_MAX_BINDINGS = 8
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_ROWS = TypeAdapter(list[tuple[str, str]])
_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_REVIEW_ROW: TypeAdapter[tuple[str, str] | None] = TypeAdapter(tuple[str, str] | None)


class ImageReviewInput(ContractModel):
    file_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=4)]
    request: Annotated[str, Field(min_length=1, max_length=8000)]

    @model_validator(mode="after")
    def signed_file_ids(self) -> Self:
        if len(set(self.file_ids)) != len(self.file_ids) or any(
            not _FILE.fullmatch(file_id) for file_id in self.file_ids
        ):
            raise ValueError("slack_image_file_ids_invalid")
        return self


@contextmanager
def _database(path: Path) -> Generator[sqlite3.Connection]:
    db = sqlite3.connect(path, timeout=1)
    try:
        with db:
            yield db
    finally:
        db.close()


def _tables(db: sqlite3.Connection) -> None:
    _ = db.executescript("""
        CREATE TABLE IF NOT EXISTS slack_image_bindings (
            tenant TEXT NOT NULL, run TEXT NOT NULL, channel TEXT NOT NULL, file TEXT NOT NULL,
            PRIMARY KEY(tenant,run,file));
        CREATE TABLE IF NOT EXISTS slack_image_reviews (
            tenant TEXT NOT NULL, run TEXT NOT NULL, invocation TEXT NOT NULL,
            digest TEXT NOT NULL, output TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(tenant,run,invocation));
    """)


def bind_files(
    database_path: Path, tenant_id: str, run_id: str, channel_id: str, file_ids: tuple[str, ...]
) -> None:
    """Only authenticated Slack admission may call; this is not a model-facing tool."""
    if not tenant_id or not run_id or not re.fullmatch(r"[CDG][A-Z0-9]{1,79}", channel_id):
        raise ValueError("slack_image_binding_scope_invalid")
    if not file_ids:
        return
    if (
        len(file_ids) > _MAX_BINDINGS
        or len(set(file_ids)) != len(file_ids)
        or any(not _FILE.fullmatch(file_id) for file_id in file_ids)
    ):
        raise ValueError("slack_image_file_ids_invalid")
    with _database(database_path) as db:
        _tables(db)
        _ = db.execute("BEGIN IMMEDIATE")
        for file_id in file_ids:
            row = _ROW.validate_python(
                db.execute(
                    "SELECT channel FROM slack_image_bindings WHERE tenant=? AND run=? AND file=?",
                    (tenant_id, run_id, file_id),
                ).fetchone()
            )
            if row is not None and row[0] != channel_id:
                raise ValueError("slack_image_binding_conflict")
            _ = db.execute(
                "INSERT OR IGNORE INTO slack_image_bindings VALUES (?,?,?,?)",
                (tenant_id, run_id, channel_id, file_id),
            )


def slack_image_review_descriptor(*, now: datetime, ready: bool) -> ToolDescriptor:
    template = research_descriptor(
        installation_id="installed:creative.image.review",
        observed_at=now,
        ready=ready,
        reason_code=None if ready else "slack_image_review_unconfigured",
    )
    schema = _JSON.validate_python(ImageReviewInput.model_json_schema())
    return template.model_copy(
        update={
            "capability_id": "creative.image.review",
            "owner": "slack_authorized_image_review",
            "input_schema": schema,
            "input_schema_sha256": contract_sha256(schema),
            "cost": template.cost.model_copy(
                update={"worst_case_units": 4, "unit": "visual_review"}
            ),
        }
    )


@dataclass(slots=True)
class SlackImageReviewTool:
    database_path: Path
    artifact_root: Path
    tenant_id: str
    token: str = field(repr=False)
    codex: CodexCli
    expected_team_id: str = ""
    opener: Callable[..., ReadResponse] = field(default=open_slack_image_request, repr=False)

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        # This new capability has no legacy unscoped executions to recover.
        # Bind caller authority before inspecting files, cached results, or network state.
        if invocation.tenant_id is None or invocation.tenant_id != self.tenant_id:
            raise ValueError("slack_image_invocation_tenant_mismatch")
        if (
            descriptor.capability_id != "creative.image.review"
            or not descriptor.readiness.ready
            or not self.tenant_id
            or not self.token
            or not self.codex.model
        ):
            raise ValueError("slack_image_review_unavailable")
        request = ImageReviewInput.model_validate(invocation.input)
        with _database(self.database_path) as db:
            _tables(db)
            _ = db.execute("BEGIN IMMEDIATE")
            rows = _ROWS.validate_python(
                db.execute(
                    "SELECT file,channel FROM slack_image_bindings WHERE tenant=? AND run=?",
                    (self.tenant_id, invocation.run_id),
                ).fetchall()
            )
            bindings = dict(rows)
            if any(file_id not in bindings for file_id in request.file_ids):
                raise ValueError("slack_image_file_not_bound")
            previous = _REVIEW_ROW.validate_python(
                db.execute(
                    """SELECT digest,output FROM slack_image_reviews
                WHERE tenant=? AND run=? AND invocation=?""",
                    (self.tenant_id, invocation.run_id, invocation.idempotency_key),
                ).fetchone()
            )
            if previous is not None:
                if previous[0] != contract_sha256(invocation) or not previous[1]:
                    raise ValueError("slack_image_review_outcome_unresolved")
                return DelegatedToolResult(
                    disposition="no_effect",
                    actual_cost_units=4,
                    output=_JSON.validate_json(previous[1]),
                )
            _ = db.execute(
                "INSERT INTO slack_image_reviews(tenant,run,invocation,digest) VALUES (?,?,?,?)",
                (
                    self.tenant_id,
                    invocation.run_id,
                    invocation.idempotency_key,
                    contract_sha256(invocation),
                ),
            )
        try:
            paths: list[Path] = []
            sources: list[JsonValue] = []
            root = self.artifact_root.resolve()
            for file_id in request.file_ids:
                source = self._files().fetch(invocation.run_id, file_id)
                paths.append(source.path)
                sources.append(
                    {
                        "file_id": source.file_id,
                        "channel_id": source.channel_id,
                        "sha256": source.sha256,
                        "artifact_relative_path": str(source.path.relative_to(root)),
                        "source_kind": "slack_signed_event_file",
                        "bytes": source.byte_size,
                    }
                )
            review = review_images(
                self.codex,
                images=tuple(paths),
                request=request.request,
                workspace_root=paths[0].parent,
                timeout_seconds=120,
            )
            output: JsonObject = {
                "review": review,
                "sources": sources,
                "artifact_root_provenance": "configured_private_artifact_root",
                "product_support_verified": False,
            }
        except Exception:  # noqa: BLE001 - sanitize provider/HTTP errors, preserve unresolved ledger.
            # Keep the started record: inference or its response may have been lost.
            raise ValueError("slack_image_review_failed") from None
        with _database(self.database_path) as db:
            _ = db.execute(
                "UPDATE slack_image_reviews SET output=? WHERE tenant=? AND run=? AND invocation=?",
                (json.dumps(output), self.tenant_id, invocation.run_id, invocation.idempotency_key),
            )
        return DelegatedToolResult(disposition="no_effect", actual_cost_units=4, output=output)

    def _files(self) -> SlackImageFiles:
        return SlackImageFiles(
            self.database_path,
            self.artifact_root,
            self.tenant_id,
            self.token,
            self.expected_team_id,
            self.opener,
        )
