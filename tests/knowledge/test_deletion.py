from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel
from typer.testing import CliRunner

from ads_booster.cli.knowledge import app
from ads_booster.cli.knowledge_runtime import settings_from_options
from ads_booster.knowledge.configuration import load_local_actor
from ads_booster.knowledge.contracts import SourceDisposition
from ads_booster.knowledge.deletion import PurgeReceipt, RetractionReceipt
from ads_booster.knowledge.erase_ledger import EraseLedger
from ads_booster.knowledge.indexing import KnowledgeIndexWorker
from ads_booster.knowledge.ingest_receipts import IngestDeliveryReceipt
from ads_booster.knowledge.repository import (
    IndexOutboxItem,
    SourceAdmissionChange,
    SqliteKnowledgeRepository,
)
from ads_booster.knowledge.retrieval import KnowledgeRetriever, SearchRequest


class BackupLocation(BaseModel):
    path: str


def test_cli_purge_replays_original_retraction_request(tmp_path: Path) -> None:
    runner = CliRunner()
    root, control, policy = tmp_path / "store", tmp_path / "control", tmp_path / "policy.json"
    options = ["--root", str(root), "--control-root", str(control), "--policy", str(policy)]
    result = runner.invoke(app, ["init", *options, "--workspace", "workspace.alpha"])
    assert result.exit_code == 0, result.output
    envelope = Path(__file__).parent / "fixtures/message-team-reporting.json"
    result = runner.invoke(app, ["ingest", *options, "--envelope", str(envelope)])
    assert result.exit_code == 0, result.output
    payload = IngestDeliveryReceipt.model_validate_json(result.output)
    source_id = payload.unit_receipts[0].receipt.source_id
    actor = load_local_actor(settings_from_options(root, control, policy))
    repository = SqliteKnowledgeRepository(root)
    now = datetime.now(UTC)
    stored = repository.read_source(actor, source_id)
    assert stored is not None
    source = stored.source
    _ = repository.change_source_admission(
        SourceAdmissionChange(
            operation_id="admit.test",
            payload_sha256="a" * 64,
            workspace_id=actor.workspace_id,
            source_id=source_id,
            expected_admission_revision=source.admission_revision,
            disposition=SourceDisposition.ADMIT,
            occurred_at=now,
            index_item=IndexOutboxItem(
                item_id="index.admit.test",
                workspace_id=actor.workspace_id,
                entity_kind="source",
                entity_id=source_id,
                revision_id=source.revision_id,
                admission_revision=source.admission_revision + 1,
            ),
        )
    )
    _ = KnowledgeIndexWorker(repository).run_once(actor.workspace_id, "index.test", now)
    assert (
        KnowledgeIndexWorker(repository).run_once(actor.workspace_id, "index.test", now).state
        == "indexed"
    )
    before = KnowledgeRetriever(repository).search(actor, SearchRequest(query="주간 보고"), now=now)
    assert before.hits
    backup = runner.invoke(app, ["backup", *options, "--destination", str(tmp_path / "backups")])
    assert backup.exit_code == 0, backup.output
    backup_path = BackupLocation.model_validate_json(backup.output).path
    restored_root = tmp_path / "restored"
    restored_options = ["--root", str(restored_root), *options[2:]]
    restored = runner.invoke(app, ["restore", *restored_options, "--backup", backup_path])
    assert restored.exit_code == 0, restored.output
    after = KnowledgeRetriever(SqliteKnowledgeRepository(restored_root)).search(
        actor, SearchRequest(query="주간 보고"), now=now
    )
    assert after.hits == before.hits
    result = runner.invoke(app, ["retract", *options, "--source", source_id])
    assert result.exit_code == 0, result.output
    operation_id = RetractionReceipt.model_validate_json(result.output).operation_id
    first = runner.invoke(app, ["purge", *options, "--request", operation_id])
    assert first.exit_code == 0, first.output
    second = runner.invoke(app, ["purge", *options, "--request", operation_id])
    assert second.exit_code == 0, second.output
    assert (
        PurgeReceipt.model_validate_json(first.output).request_id
        == PurgeReceipt.model_validate_json(second.output).request_id
    )
    assert EraseLedger(control).export("workspace.alpha").sequence == 1
    actor = load_local_actor(settings_from_options(root, control, policy))
    assert SqliteKnowledgeRepository(root).read_source(actor, source_id) is None
