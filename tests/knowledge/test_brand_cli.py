from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter
from typer.testing import CliRunner

from ads_booster.cli.knowledge import app
from ads_booster.knowledge.configuration import LocalKnowledgeIdentity, LocalKnowledgePolicy
from ads_booster.knowledge.contracts import GrantCapability, OperationReceipt
from ads_booster.knowledge.errors import AccessDeniedError
from ads_booster.knowledge.maintenance import KnowledgeOwner
from ads_booster.knowledge.repository import RepositoryConflictError

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import Result

    from ads_booster.transport.json_types import JsonObject

_COUNTS: TypeAdapter[tuple[int, ...]] = TypeAdapter(tuple[int, ...])
_ROWS: TypeAdapter[list[tuple[str, str]]] = TypeAdapter(list[tuple[str, str]])


@dataclass(frozen=True, slots=True)
class BrandCli:
    directory: Path

    @property
    def options(self) -> list[str]:
        return [
            "--root",
            str(self.directory / "store"),
            "--control-root",
            str(self.directory / "control"),
            "--policy",
            str(self.policy_path),
        ]

    @property
    def policy_path(self) -> Path:
        return self.directory / "control/policy.json"

    def register(self, request: JsonObject) -> Result:
        path = self.directory / "request.json"
        _ = path.write_text(json.dumps(request))
        return CliRunner().invoke(app, ["brand", "register", *self.options, "--request", str(path)])

    def snapshot(self) -> tuple[tuple[int, ...], list[tuple[str, str]]]:
        with closing(sqlite3.connect(self.directory / "store/index.sqlite")) as connection:
            counts = _COUNTS.validate_python(
                connection.execute("""SELECT
                (SELECT count(*) FROM brands), (SELECT count(*) FROM brand_events),
                (SELECT count(*) FROM memory_documents), (SELECT count(*) FROM memory_revisions),
                (SELECT count(*) FROM operations), (SELECT count(*) FROM operation_heads)
                """).fetchone()
            )
            heads = _ROWS.validate_python(
                connection.execute(
                    "SELECT document_id,revision_id FROM memory_heads ORDER BY document_id"
                ).fetchall()
            )
        return counts, heads


@pytest.fixture
def brand_cli(tmp_path: Path) -> BrandCli:
    cli = BrandCli(tmp_path)
    initialized = CliRunner().invoke(app, ["init", *cli.options, "--workspace", "workspace.cli"])
    assert initialized.exit_code == 0, initialized.output
    return cli


def test_brand_registration_replays_original_receipt_and_heads(brand_cli: BrandCli) -> None:
    # Given
    request: JsonObject = {"operation_id": "operation.brand", "name": "Synthetic Brand"}
    first = brand_cli.register(request)
    assert first.exit_code == 0, first.output
    before = brand_cli.snapshot()

    # When
    replay = brand_cli.register(request)

    # Then
    assert replay.exit_code == 0, replay.output
    assert OperationReceipt.model_validate_json(
        replay.output
    ) == OperationReceipt.model_validate_json(first.output)
    assert brand_cli.snapshot() == before


def test_brand_same_operation_changed_name_conflicts(brand_cli: BrandCli) -> None:
    # Given
    assert brand_cli.register({"operation_id": "operation.brand", "name": "First"}).exit_code == 0
    before = brand_cli.snapshot()

    # When
    result = brand_cli.register({"operation_id": "operation.brand", "name": "Changed"})

    # Then
    assert isinstance(result.exception, RepositoryConflictError)
    assert result.exception.code == "operation_idempotency_conflict"
    assert brand_cli.snapshot() == before


def test_brand_cross_workspace_claim_is_rejected(brand_cli: BrandCli) -> None:
    # Given
    before = brand_cli.snapshot()

    # When
    result = brand_cli.register(
        {"operation_id": "operation.brand", "name": "Brand", "workspace_id": "workspace.other"}
    )

    # Then
    assert result.exit_code != 0
    assert str(result.exception) == "knowledge_brand_workspace_claim_rejected"
    assert brand_cli.snapshot() == before


def test_brand_replay_rechecks_current_local_write_capability(brand_cli: BrandCli) -> None:
    # Given
    request: JsonObject = {"operation_id": "operation.brand", "name": "Brand"}
    assert brand_cli.register(request).exit_code == 0
    policy = LocalKnowledgePolicy.model_validate_json(brand_cli.policy_path.read_bytes())
    _ = brand_cli.policy_path.write_text(
        policy.model_copy(
            update={
                "capabilities": (GrantCapability.READ,),
            }
        ).model_dump_json(by_alias=True)
    )
    before = brand_cli.snapshot()

    # When
    result = brand_cli.register(request)

    # Then
    assert isinstance(result.exception, AccessDeniedError)
    assert brand_cli.snapshot() == before


def test_brand_replay_rejects_changed_local_member(brand_cli: BrandCli) -> None:
    # Given
    request: JsonObject = {"operation_id": "operation.brand", "name": "Brand"}
    assert brand_cli.register(request).exit_code == 0
    path = brand_cli.directory / "control/identity.json"
    identity = LocalKnowledgeIdentity.model_validate_json(path.read_bytes())
    _ = path.write_text(
        identity.model_copy(update={"member_id": "member.other"}).model_dump_json(by_alias=True)
    )
    before = brand_cli.snapshot()

    # When
    result = brand_cli.register(request)

    # Then
    assert result.exit_code != 0
    assert brand_cli.snapshot() == before
    with KnowledgeOwner(brand_cli.directory / "store", "after-rejected-member"):
        pass


def test_brand_replay_rechecks_private_configuration(brand_cli: BrandCli) -> None:
    # Given
    request: JsonObject = {"operation_id": "operation.brand", "name": "Brand"}
    assert brand_cli.register(request).exit_code == 0
    brand_cli.policy_path.chmod(0o644)
    before = brand_cli.snapshot()

    # When
    result = brand_cli.register(request)

    # Then
    assert str(result.exception) == "knowledge_configuration_permissions_invalid"
    assert brand_cli.snapshot() == before
