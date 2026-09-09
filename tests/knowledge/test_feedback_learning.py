from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from ads_booster.agent.service.learning_admission import LearningAdmissionError
from ads_booster.contracts.agent_run import AgentRecordKind, ToolInvocation, contract_sha256
from ads_booster.knowledge.learning_contracts import (
    EXPERIENCE_INPUT_EXCERPT_BYTES,
    EXPERIENCE_OUTPUT_EXCERPT_BYTES,
    ExperienceEvidenceCompleteness,
    ExperienceOutcome,
    LearningRoundState,
)
from ads_booster.knowledge.maintenance_jobs import CanonicalJobProcessor
from ads_booster.knowledge.operation_contracts import KnowledgeJob
from ads_booster.knowledge.repository_learning import LearningReviewCoordinator
from ads_booster.knowledge.runtime import SqliteBatchFlusher
from ads_booster.tools.web_search import WebSearch, search_descriptor
from tests.knowledge.batch_runtime_support import batch_fixture
from tests.knowledge.feedback_learning_support import (
    AdmittedSource,
    ReceiptDisposition,
    SharedMessage,
    TerminalWrite,
    admit_shared_source,
    admitted_run,
    append_terminal_receipt,
    learning_fixture,
    receipt_record,
    terminal_receipt,
    terminal_step,
)

_JOB_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


def test_workspace_tenth_turn_seals_one_round_and_preserves_tail(tmp_path: Path) -> None:
    fixture = learning_fixture(tmp_path)

    sources = tuple(
        admit_shared_source(fixture, SharedMessage(f"message.{ordinal}", "run.shared"))
        for ordinal in range(1, 10)
    )
    for source in sources:
        admission = fixture.learning.admit_turn(
            source.binding, source.event, source.receipt, at=source.event.created_at
        )
        assert admission.ready_batch_ids == ()
    assert (
        SqliteBatchFlusher(fixture.knowledge).flush_ready_batches(
            "workspace.alpha", sources[-1].event.created_at + timedelta(minutes=2)
        )
        == 0
    )
    tenth = admit_shared_source(fixture, SharedMessage("message.10", "run.shared"))
    ready = fixture.learning.admit_turn(
        tenth.binding, tenth.event, tenth.receipt, at=tenth.event.created_at
    )
    tail = admit_shared_source(fixture, SharedMessage("message.11", "run.shared"))

    assert len(ready.ready_batch_ids) == 1
    assert (
        fixture.learning.admit_turn(
            tail.binding, tail.event, tail.receipt, at=tail.event.created_at
        ).ready_batch_ids
        == ()
    )
    round_ = fixture.learning.rounds("workspace.alpha")[0]
    assert (round_.state, round_.turn_count, round_.receipt_count, round_.end_turn_watermark) == (
        LearningRoundState.READY,
        10,
        0,
        tenth.event.message_id,
    )
    assert fixture.learning.counter("workspace.alpha").conversation_turns == 1
    assert (
        fixture.knowledge.curation_batch(tenth.binding.actor, ready.ready_batch_ids[0]) is not None
    )


def test_workspace_round_wakes_existing_batches_per_grant_partition(tmp_path: Path) -> None:
    fixture = learning_fixture(tmp_path)

    for ordinal in range(1, 6):
        editor = admit_shared_source(fixture, SharedMessage(f"editor.{ordinal}", "run.editor"))
        colleague = admit_shared_source(
            fixture, SharedMessage(f"colleague.{ordinal}", "run.colleague", "member.colleague")
        )
        _ = fixture.learning.admit_turn(
            editor.binding, editor.event, editor.receipt, at=editor.event.created_at
        )
        _ = fixture.learning.admit_turn(
            colleague.binding, colleague.event, colleague.receipt, at=colleague.event.created_at
        )
    round_ = fixture.learning.rounds("workspace.alpha")[0]

    assert round_.turn_count == 10
    assert len(fixture.learning.partition_keys(round_.round_id)) == 2
    assert len(fixture.learning.ready_batches(round_.round_id)) == 2


def test_replay_and_restart_do_not_open_a_second_round(tmp_path: Path) -> None:
    fixture = learning_fixture(tmp_path)

    sources = tuple(
        admit_shared_source(fixture, SharedMessage(f"message.{ordinal}", "run.shared"))
        for ordinal in range(1, 11)
    )
    for source in sources:
        _ = fixture.learning.admit_turn(
            source.binding, source.event, source.receipt, at=source.event.created_at
        )
        assert (
            fixture.learning.admit_turn(
                source.binding, source.event, source.receipt, at=source.event.created_at
            ).ready_batch_ids
            == ()
        )
    restarted = LearningReviewCoordinator(fixture.knowledge)

    assert len(restarted.rounds("workspace.alpha")) == 1
    assert restarted.counter("workspace.alpha").conversation_turns == 0


def test_receipt_and_experience_admit_atomically_from_stored_run_and_source(tmp_path: Path) -> None:
    fixture = learning_fixture(tmp_path)
    source = admit_shared_source(fixture, SharedMessage("message.1", "run.receipt"))
    run, invocation_sha256 = admitted_run(fixture, source)
    receipt = terminal_receipt(run, invocation_sha256)
    admission = fixture.terminal.for_receipt(source.binding, source.receipt, receipt)

    def fail_after_outbox(connection: sqlite3.Connection) -> None:
        admission(connection)
        message = "controlled_learning_outbox_failure"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="controlled_learning_outbox_failure"):
        _ = fixture.agent_runs.append_step(
            run,
            terminal_step(run),
            state=run.state,
            expected_revision=run.revision,
            records=(receipt_record(run, receipt),),
            admission=fail_after_outbox,
        )
    assert tuple(
        record.record_id for record in fixture.agent_runs.records(run.tenant_id, run.run_id)
    ) == (f"{run.run_id}:invocation.1",)
    assert fixture.terminal.pending(run.run_id) == ()

    updated = append_terminal_receipt(TerminalWrite(fixture, source, run, receipt))
    assert (
        fixture.agent_runs.records(updated.tenant_id, updated.run_id)[-1].record_id
        == receipt.receipt_id
    )
    assert fixture.terminal.pending(updated.run_id)[0].receipt_id == receipt.receipt_id
    with (
        pytest.raises(LearningAdmissionError, match="learning_receipt_replay"),
        closing(sqlite3.connect(fixture.agent_runs.database_path)) as connection,
    ):
        fixture.terminal.for_receipt(source.binding, source.receipt, receipt)(connection)


def test_terminal_failures_and_unknown_effects_count_but_never_promote_success(
    tmp_path: Path,
) -> None:
    fixture = learning_fixture(tmp_path)

    dispositions: tuple[ReceiptDisposition, ...] = ("succeeded",) * 8 + (
        "unknown_side_effect",
        "failed",
    )
    for ordinal, disposition in enumerate(dispositions, start=1):
        source = admit_shared_source(fixture, SharedMessage(f"receipt.{ordinal}", f"run.{ordinal}"))
        run, invocation_sha256 = admitted_run(fixture, source)
        _ = append_terminal_receipt(
            TerminalWrite(
                fixture,
                source,
                run,
                terminal_receipt(run, invocation_sha256, disposition=disposition),
            )
        )
    round_ = fixture.learning.rounds("workspace.alpha")[0]

    assert (
        round_.receipt_count,
        round_.experience_refs[-2].outcome,
        round_.experience_refs[-1].outcome,
    ) == (
        10,
        ExperienceOutcome.UNKNOWN_SIDE_EFFECT,
        ExperienceOutcome.FAILED,
    )
    assert fixture.learning.successful_experiences(round_.round_id) == round_.experience_refs[:8]


def test_ten_observed_reads_count_as_terminal_work_without_claiming_effect_success(
    tmp_path: Path,
) -> None:
    fixture = learning_fixture(tmp_path)

    for ordinal in range(1, 11):
        source = admit_shared_source(
            fixture, SharedMessage(f"read.{ordinal}", f"run.read.{ordinal}")
        )
        run, invocation_sha256 = admitted_run(
            fixture,
            source,
            tool_input={"query": "evidence-backed marketing procedure"},
        )
        invocation = ToolInvocation.model_validate(
            next(
                record.payload
                for record in fixture.agent_runs.records(run.tenant_id, run.run_id)
                if record.kind is AgentRecordKind.INVOCATION
            )
        )
        read = WebSearch(
            search=lambda _query: [
                {
                    "href": "https://example.com/evidence",
                    "title": "Observed evidence",
                    "body": "A cited observation returned by the read adapter.",
                }
            ]
        ).execute(invocation, search_descriptor(now=source.event.created_at))
        assert read.disposition == "no_effect"
        _ = append_terminal_receipt(
            TerminalWrite(
                fixture,
                source,
                run,
                terminal_receipt(run, invocation_sha256, disposition=read.disposition),
                capability_id="research.search",
            )
        )

    round_ = fixture.learning.rounds("workspace.alpha")[0]
    assert round_.receipt_count == 10
    assert {experience.outcome for experience in round_.experience_refs} == {
        ExperienceOutcome.OBSERVED
    }
    assert fixture.learning.successful_experiences(round_.round_id) == round_.experience_refs


def test_observed_read_excludes_private_background_learning_tools_and_receipt_replay(
    tmp_path: Path,
) -> None:
    fixture = learning_fixture(tmp_path)
    cases = (
        (SharedMessage("read.private", "run.private", private=True), "research.search"),
        (SharedMessage("read.background", "background.read"), "research.search"),
        (SharedMessage("read.learning", "run.learning"), "source_read"),
    )
    for message, capability_id in cases:
        source = admit_shared_source(fixture, message)
        run, invocation_sha256 = admitted_run(fixture, source)
        _ = append_terminal_receipt(
            TerminalWrite(
                fixture,
                source,
                run,
                terminal_receipt(run, invocation_sha256, disposition="no_effect"),
                capability_id=capability_id,
            )
        )

    source = admit_shared_source(fixture, SharedMessage("read.replay", "run.replay"))
    run, invocation_sha256 = admitted_run(fixture, source)
    receipt = terminal_receipt(run, invocation_sha256, disposition="no_effect")
    _ = append_terminal_receipt(
        TerminalWrite(
            fixture,
            source,
            run,
            receipt,
            capability_id="research.search",
        )
    )
    with (
        pytest.raises(LearningAdmissionError, match="learning_receipt_replay"),
        closing(sqlite3.connect(fixture.agent_runs.database_path)) as connection,
    ):
        fixture.terminal.for_receipt(
            source.binding,
            source.receipt,
            receipt,
            capability_id="research.search",
        )(connection)

    assert fixture.learning.counter("workspace.alpha").terminal_tool_receipts == 1
    assert fixture.learning.rounds("workspace.alpha") == ()


def test_canonical_tool_output_evidence_reaches_actual_curation_request(
    tmp_path: Path,
) -> None:
    fixture = learning_fixture(tmp_path)
    output_marker = "result-marker-only-in-canonical-tool-output"

    sources = tuple(
        admit_shared_source(
            fixture,
            SharedMessage(f"evidence.{ordinal}", f"run.evidence.{ordinal}"),
        )
        for ordinal in range(1, 11)
    )
    for ordinal, source in enumerate(sources, start=1):
        run, invocation_sha256 = admitted_run(
            fixture,
            source,
            tool_input={"query": f"input-marker-{ordinal}"},
        )
        output: JsonObject = {"finding": f"{output_marker}-{ordinal}"}
        receipt = terminal_receipt(
            run,
            invocation_sha256,
            disposition="no_effect",
            output=output,
        )
        _ = append_terminal_receipt(
            TerminalWrite(
                fixture,
                source,
                run,
                receipt,
                capability_id="research.search",
                output=output,
            )
        )

    source = sources[-1]
    expected_input: JsonObject = {"query": "input-marker-10"}
    expected_output: JsonObject = {"finding": f"{output_marker}-10"}
    with fixture.knowledge.connection() as connection:
        row = _JOB_ROW.validate_python(
            connection.execute(
                "SELECT job_json FROM jobs WHERE job_id=?",
                (source.receipt.curation_job_id,),
            ).fetchone()
        )
    assert row is not None
    job = KnowledgeJob.model_validate_json(str(row[0]))
    dependencies = batch_fixture(tmp_path / "curation-dependencies")
    processor = CanonicalJobProcessor(
        fixture.knowledge,
        source.binding.actor,
        dependencies.runtime.jobs.curation,
        dependencies.runtime.jobs.memory,
    )
    try:
        request = processor.build_curation_work(job).request
    finally:
        dependencies.close()

    assert output_marker not in source.event.text
    assert request.learning_review is not None
    evidence = request.learning_review.experience_refs[-1].evidence
    assert evidence is not None
    assert evidence.input_completeness == "complete"
    assert evidence.output_completeness == "complete"
    assert evidence.input_json_excerpt is not None
    assert evidence.output_json_excerpt is not None
    assert "input-marker-10" in evidence.input_json_excerpt
    assert output_marker in evidence.output_json_excerpt
    assert evidence.input_sha256 == contract_sha256(expected_input)
    assert evidence.output_sha256 == contract_sha256(expected_output)


def test_oversized_terminal_evidence_is_explicitly_truncated_with_full_digests(
    tmp_path: Path,
) -> None:
    fixture = learning_fixture(tmp_path)
    source = admit_shared_source(fixture, SharedMessage("evidence.large", "run.evidence.large"))
    tool_input: JsonObject = {"query": "input-marker-" + "i" * EXPERIENCE_INPUT_EXCERPT_BYTES}
    output: JsonObject = {"finding": "output-marker-" + "o" * EXPERIENCE_OUTPUT_EXCERPT_BYTES}
    run, invocation_sha256 = admitted_run(fixture, source, tool_input=tool_input)
    receipt = terminal_receipt(run, invocation_sha256, output=output)

    _ = append_terminal_receipt(
        TerminalWrite(
            fixture,
            source,
            run,
            receipt,
            capability_id="research.search",
            output=output,
        )
    )

    experience = fixture.terminal.pending(run.run_id)[0]
    evidence = experience.evidence
    assert evidence is not None
    assert (
        evidence.input_completeness,
        evidence.output_completeness,
    ) == (
        ExperienceEvidenceCompleteness.TRUNCATED,
        ExperienceEvidenceCompleteness.TRUNCATED,
    )
    assert evidence.input_json_excerpt is not None
    assert evidence.output_json_excerpt is not None
    assert len(evidence.input_json_excerpt.encode()) <= EXPERIENCE_INPUT_EXCERPT_BYTES
    assert len(evidence.output_json_excerpt.encode()) <= EXPERIENCE_OUTPUT_EXCERPT_BYTES
    assert (evidence.input_sha256, evidence.output_sha256) == (
        contract_sha256(tool_input),
        contract_sha256(output),
    )
    assert "evidence" not in experience.model_copy(update={"evidence": None}).model_dump(
        mode="json"
    )


def test_malformed_terminal_output_digest_rolls_back_receipt_and_experience(
    tmp_path: Path,
) -> None:
    fixture = learning_fixture(tmp_path)
    source = admit_shared_source(
        fixture, SharedMessage("evidence.malformed", "run.evidence.malformed")
    )
    run, invocation_sha256 = admitted_run(fixture, source)
    expected: JsonObject = {"finding": "expected"}
    tampered: JsonObject = {"finding": "tampered"}
    receipt = terminal_receipt(run, invocation_sha256, output=expected)

    with pytest.raises(LearningAdmissionError, match="learning_output_digest_conflict"):
        _ = append_terminal_receipt(
            TerminalWrite(
                fixture,
                source,
                run,
                receipt,
                capability_id="research.search",
                output=tampered,
            )
        )

    assert tuple(
        record.kind for record in fixture.agent_runs.records(run.tenant_id, run.run_id)
    ) == (AgentRecordKind.INVOCATION,)
    assert fixture.terminal.pending(run.run_id) == ()


def test_stale_source_cannot_attach_terminal_output_evidence(tmp_path: Path) -> None:
    fixture = learning_fixture(tmp_path)
    source = admit_shared_source(fixture, SharedMessage("evidence.stale", "run.evidence.stale"))
    _ = admit_shared_source(
        fixture,
        SharedMessage("evidence.stale", "run.evidence.stale", revision=2),
    )
    run, invocation_sha256 = admitted_run(fixture, source)
    output: JsonObject = {"finding": "stale-source-output"}
    receipt = terminal_receipt(run, invocation_sha256, output=output)

    with pytest.raises(LearningAdmissionError, match="learning_source_revision_stale"):
        _ = append_terminal_receipt(
            TerminalWrite(
                fixture,
                source,
                run,
                receipt,
                capability_id="research.search",
                output=output,
            )
        )

    assert tuple(
        record.kind for record in fixture.agent_runs.records(run.tenant_id, run.run_id)
    ) == (AgentRecordKind.INVOCATION,)


def test_forged_run_binding_cannot_attach_terminal_output_evidence(tmp_path: Path) -> None:
    fixture = learning_fixture(tmp_path)
    source = admit_shared_source(fixture, SharedMessage("evidence.forged", "run.evidence.forged"))
    run, invocation_sha256 = admitted_run(fixture, source)
    output: JsonObject = {"finding": "forged-binding-output"}
    receipt = terminal_receipt(run, invocation_sha256, output=output)
    forged = AdmittedSource(
        source.binding.model_copy(update={"binding_id": "binding.forged"}),
        source.event,
        source.receipt,
    )

    with pytest.raises(LearningAdmissionError, match="learning_stored_binding_conflict"):
        _ = append_terminal_receipt(
            TerminalWrite(
                fixture,
                forged,
                run,
                receipt,
                capability_id="research.search",
                output=output,
            )
        )

    assert tuple(
        record.kind for record in fixture.agent_runs.records(run.tenant_id, run.run_id)
    ) == (AgentRecordKind.INVOCATION,)


def test_source_revision_invalidates_pending_learning_before_batch_claim(tmp_path: Path) -> None:
    fixture = learning_fixture(tmp_path)

    sources = tuple(
        admit_shared_source(fixture, SharedMessage(f"message.{ordinal}", "run.shared"))
        for ordinal in range(1, 11)
    )
    for source in sources:
        _ = fixture.learning.admit_turn(
            source.binding, source.event, source.receipt, at=source.event.created_at
        )
    revised = admit_shared_source(fixture, SharedMessage("message.10", "run.shared", revision=2))
    _ = fixture.learning.invalidate_source(
        revised.binding.actor,
        source_id=sources[-1].receipt.source_id,
        source_revision_id=revised.receipt.source_revision_id,
    )

    round_ = fixture.learning.rounds("workspace.alpha")[0]
    assert fixture.learning.ready_batches(round_.round_id) == ()
    assert round_.state is LearningRoundState.CANCELLED


def test_user_event_is_not_forged_for_tool_result_or_private_text(tmp_path: Path) -> None:
    fixture = learning_fixture(tmp_path)
    private = admit_shared_source(
        fixture, SharedMessage("private.1", "run.private", "member.private", private=True)
    )
    source = admit_shared_source(fixture, SharedMessage("message.1", "run.tool"))
    run, invocation_sha256 = admitted_run(fixture, source)

    assert (
        fixture.learning.admit_turn(
            private.binding, private.event, private.receipt, at=private.event.created_at
        ).ready_batch_ids
        == ()
    )
    _ = append_terminal_receipt(
        TerminalWrite(fixture, source, run, terminal_receipt(run, invocation_sha256))
    )
    pending = fixture.terminal.pending(run.run_id)[0]
    assert pending.schema_version == "knowledge.agent-experience-ref.v1"
    assert fixture.learning.counter("workspace.alpha").conversation_turns == 0
