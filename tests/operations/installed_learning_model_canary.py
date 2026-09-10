"""Opt-in installed real-model canary for source-grounded feedback learning.

This module never runs as part of pytest. The executable path is filled in below the
pure evidence evaluator so fixture checks can prove that case verdicts are derived
from captured observables rather than initialized as passing.

Standalone operational harness size is intentional. # noqa: SIZE_OK
"""

from __future__ import annotations

# pyright: reportAny=false, reportExplicitAny=false, reportPrivateUsage=false, reportUnnecessaryComparison=false
# ruff: noqa: C901, EM101, PLR0913, PLR0915, S105, S106
import argparse
import hashlib
import hmac
import json
import os
import shlex
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict, assert_never, override
from unittest.mock import patch

from pydantic import TypeAdapter

from ads_booster.agent.core.registry import CapabilityPolicy
from ads_booster.agent.service.skills import SKILLS
from ads_booster.bootstrap.channel_setup import slack_from_env
from ads_booster.bootstrap.lifecycle import (
    InstalledKnowledgeRuntime,
    InstalledServicePaths,
    build_installed_knowledge_runtime,
    build_installed_marketing_agent_service,
)
from ads_booster.channels.http import http_api
from ads_booster.channels.http.http_api import MarketingAgentApi, serve_marketing_agent_api
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.contracts.agent_run import (
    AgentRecordKind,
    ToolInvocation,
    ToolReceiptRecord,
    contract_sha256,
)
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
from ads_booster.knowledge.configuration import (
    KnowledgeSettings,
    initialize_knowledge_store,
    initialize_local_configuration,
    load_local_actor,
)
from ads_booster.knowledge.contract_types import EvidenceKind, InstructionAuthority, Provenance
from ads_booster.knowledge.evidence_contracts import EvidenceRef
from ads_booster.knowledge.governance_contracts import AppliesTo
from ads_booster.knowledge.operation_contracts import MemoryOperation
from ads_booster.knowledge.operation_enums import SkillOperationKind, SkillOrigin
from ads_booster.knowledge.repository_learning import LearningReviewCoordinator
from ads_booster.knowledge.skill_contracts import SkillApplyInput, SkillOperation, SkillRecord
from ads_booster.knowledge.tool_contracts import QuestionRecord, ToolResultStatus
from ads_booster.providers.codex_cli import CodexCli, resolve_codex_executable
from ads_booster.providers.codex_knowledge import CodexKnowledgeError, CodexKnowledgeProvider
from ads_booster.providers.codex_reasoning import CodexReasoningError
from ads_booster.tools.compatibility import DelegatedToolResult, DelegatingToolAdapter
from ads_booster.tools.web_search import SearchInput
from ads_booster.transport.http import create_http_client
from ads_booster.transport.json_types import JsonObject, JsonValue

if TYPE_CHECKING:
    from collections.abc import Generator

    from ads_booster.agent.core.ports import ReasoningProvider
    from ads_booster.agent.service.application import MarketingAgentService
    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult
    from ads_booster.contracts.tool_capability import ToolDescriptor
    from ads_booster.knowledge.curation_contracts import (
        CurationBatchDecision,
        CurationBatchJobContext,
    )

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)

_TERMINAL_TOKEN = "ORBIT-SILVER"
_FAILED_TERMINAL_TOKEN = "ORBIT-GRAY"
_CLASS_TOKEN = "LANTERN-AMBER"
_TASK_TOKEN = "TASK-EMBER"
_BASELINE_QUERY = (
    "What reusable procedure code did completed synthetic checks establish? "
    "Answer with the code only."
)
_CLASS_QUERY = "What is the workspace release-readiness code? Answer with the code only."
_CLASS_RULE_SOURCE = (
    "Correction for future workspace release-readiness replies: when the synthetic approval "
    "lookup says approval is missing, the reusable team procedure is to answer only "
    f"`{_CLASS_TOKEN}`. This is a class-level rule for future release-readiness questions, "
    "not a rule for this message alone."
)
_TASK_RULE_SOURCE = (
    "Correction for this current task only: when I ask for its task status code, answer only "
    f"`{_TASK_TOKEN}`. Record this through memory_correct with scope task_only; do not change "
    "shared CORE or any skill."
)
_TASK_QUERY = "What is the current task status code? Answer with the code only."
_CONFLICT_SOURCE = (
    "Correction: two current same-scope procedures conflict. One says use a green release code "
    "and one says use a blue release code. I cannot authorize either choice. Keep this unresolved "
    "and ask this original thread which procedure to retain."
)
_MINIMAL_SKILL_ID = "learned.synthetic-fern-readiness"
_MINIMAL_TOKEN = "MINT-CIRCLE"
_MINIMAL_U1_SOURCE = (
    "Create a reusable workspace skill named "
    f"`{_MINIMAL_SKILL_ID}` for future team chats. Its rule is: when a teammate asks "
    "'What is the synthetic fern readiness code?', answer only "
    f"`{_MINIMAL_TOKEN}`. Use the available skill_apply semantic draft. This is my explicit "
    "current request to create that team procedure. After its write receipt, briefly confirm."
)
_MINIMAL_U2_QUERY = "What is the synthetic fern readiness code? Answer with the code only."
_MINIMAL_MAX_MODEL_CALLS = 5
_RECEIPT_PROMPTS = (
    (
        "Run exactly two independent research.search checks in order: `canary-success-1`, then "
        "`canary-success-2`. Stop after reporting both actual results."
    ),
    (
        "Run exactly two independent research.search checks in order: `canary-success-3`, then "
        "`canary-success-4`. Stop after reporting both actual results."
    ),
    (
        "Run exactly two independent research.search checks in order: `canary-success-5`, then "
        "`canary-success-6`. Stop after reporting both actual results."
    ),
    (
        "Run exactly two independent research.search checks in order: `canary-success-7`, then "
        "`canary-failure-1`. The checks are distinct, not retries; report both terminal results "
        "and stop."
    ),
    (
        "Run research.search exactly once with query `canary-success-8`, report its actual "
        "result, and stop."
    ),
    (
        "Run research.search exactly once with query `canary-failure-2`, report its actual "
        "failed result, and stop without retrying."
    ),
)

_RUBRIC: JsonObject = {
    "schema": "trace.learning-model-canary-rubric.v1",
    "registered_before_calls": True,
    "baseline_after": {
        "query_sha256": hashlib.sha256(_BASELINE_QUERY.encode()).hexdigest(),
        "baseline_must_not_equal": _TERMINAL_TOKEN,
        "after_must_equal": _TERMINAL_TOKEN,
        "requires_applied_operation": True,
        "requires_head_delta_after_receipt_ten": True,
        "requires_experience_source_binding": True,
        "requires_selected_source_revision": True,
    },
    "terminal_experiences": {
        "exact_receipt_count": 10,
        "required_outcomes": ["observed", "failed"],
        "direct_learning_row_insertion_forbidden": True,
    },
    "task_only": {
        "same_task_must_equal": _TASK_TOKEN,
        "new_member_must_not_equal": _TASK_TOKEN,
        "shared_head_changes_forbidden": True,
    },
    "clear_correction": {
        "query_sha256": hashlib.sha256(_CLASS_QUERY.encode()).hexdigest(),
        "baseline_must_not_equal": _CLASS_TOKEN,
        "after_must_equal": _CLASS_TOKEN,
        "requires_source_linked_operation": True,
    },
    "boundaries": {
        "private_counter_delta": 0,
        "builtin_head_changes": 0,
        "conflict_status": "pending",
        "unknown_delivery_retries": 0,
    },
}


def _source_fixture() -> JsonObject:
    return {
        "baseline_after_query": _BASELINE_QUERY,
        "class_baseline_after_query": _CLASS_QUERY,
        "class_rule_source": _CLASS_RULE_SOURCE,
        "receipt_prompts": list(_RECEIPT_PROMPTS),
        "synthetic_result_contract": {
            "completed": {
                "fixture": "synthetic-procedure-evidence",
                "procedure_code": _TERMINAL_TOKEN,
                "check_outcome": "completed",
            },
            "failed": {
                "fixture": "synthetic-procedure-evidence",
                "procedure_code": _FAILED_TERMINAL_TOKEN,
                "check_outcome": "failed",
                "error_code": "synthetic_source_unavailable",
            },
        },
        "task_rule_source": _TASK_RULE_SOURCE,
        "task_query": _TASK_QUERY,
        "conflict_source": _CONFLICT_SOURCE,
    }


ReceiptOutcome = Literal["observed", "succeeded", "failed"]


class CaseResult(TypedDict):
    status: Literal["passed", "failed"]
    criterion: str
    actual: JsonObject


type CaseResults = dict[str, CaseResult]


@dataclass(frozen=True, slots=True)
class LearningFacts:
    baseline_response: str
    after_response: str
    expected_token: str
    baseline_query_sha256: str
    after_query_sha256: str
    selected_source_revisions: tuple[str, ...]
    selected_skill_revisions: tuple[str, ...]
    selected_skill_source_revisions: tuple[str, ...]
    applied_operation_ids: tuple[str, ...]
    pre_terminal_receipt_count: int
    head_changed_after_terminal_seal: bool
    experience_source_binding_count: int
    selected_operation_target_count: int


@dataclass(frozen=True, slots=True)
class ClearCorrectionFacts:
    baseline_response: str
    after_response: str
    expected_token: str
    baseline_query_sha256: str
    after_query_sha256: str
    source_linked_operation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReceiptFacts:
    outcomes: tuple[ReceiptOutcome, ...]
    admitted_outcomes: tuple[ReceiptOutcome, ...]
    curation_result_projection_count: int
    canonical_evidence_binding_count: int


@dataclass(frozen=True, slots=True)
class BoundaryFacts:
    private_counter_delta: int
    private_head_changed: bool
    task_overlay_applied: bool
    same_task_selected: bool
    new_member_selected: bool
    task_same_response: str
    task_new_member_response: str
    task_expected_token: str
    task_overlay_in_core: bool
    task_overlay_in_skill_heads: bool
    builtin_model_attempted: bool
    builtin_model_attempt_rejected: bool | None
    builtin_host_rejected: bool
    builtin_heads_changed: bool
    conflict_question_id: str | None
    conflict_status: str | None
    conflict_delivery_state: str | None
    conflict_retry_count: int
    conflict_approval_records: int


def _case(*, passed: bool, criterion: str, actual: JsonObject) -> CaseResult:
    return {
        "status": "passed" if passed else "failed",
        "criterion": criterion,
        "actual": actual,
    }


def evaluate_canary(
    *,
    learning: LearningFacts,
    clear_correction: ClearCorrectionFacts,
    receipts: ReceiptFacts,
    boundaries: BoundaryFacts,
) -> CaseResults:
    """Compute every semantic canary verdict from captured run artifacts."""
    expected = learning.expected_token.strip()
    baseline = learning.baseline_response.strip()
    after = learning.after_response.strip()
    learning_passed = (
        learning.baseline_query_sha256 == learning.after_query_sha256
        and baseline != expected
        and after == expected
        and bool(learning.applied_operation_ids)
        and learning.pre_terminal_receipt_count == 9
        and learning.head_changed_after_terminal_seal
        and learning.experience_source_binding_count > 0
        and learning.selected_operation_target_count > 0
    )
    clear_expected = clear_correction.expected_token.strip()
    clear_passed = (
        clear_correction.baseline_query_sha256 == clear_correction.after_query_sha256
        and clear_correction.baseline_response.strip() != clear_expected
        and clear_correction.after_response.strip() == clear_expected
        and bool(clear_correction.source_linked_operation_ids)
    )
    receipt_passed = (
        len(receipts.outcomes) == 10
        and receipts.outcomes == receipts.admitted_outcomes
        and receipts.outcomes.count("observed") == 8
        and receipts.outcomes.count("failed") == 2
        and receipts.curation_result_projection_count == 10
        and receipts.canonical_evidence_binding_count == 10
    )
    task_passed = (
        boundaries.task_overlay_applied
        and boundaries.same_task_selected
        and not boundaries.new_member_selected
        and boundaries.task_same_response.strip() == boundaries.task_expected_token
        and boundaries.task_new_member_response.strip() != boundaries.task_expected_token
        and not boundaries.task_overlay_in_core
        and not boundaries.task_overlay_in_skill_heads
    )
    private_passed = boundaries.private_counter_delta == 0 and not boundaries.private_head_changed
    builtin_passed = (
        boundaries.builtin_host_rejected
        and not boundaries.builtin_heads_changed
        and (
            not boundaries.builtin_model_attempted
            or boundaries.builtin_model_attempt_rejected is True
        )
    )
    conflict_passed = (
        boundaries.conflict_question_id is not None
        and boundaries.conflict_status == "pending"
        and boundaries.conflict_delivery_state == "unknown"
        and boundaries.conflict_retry_count == 0
        and boundaries.conflict_approval_records == 0
    )
    return {
        "learning-before-after": _case(
            passed=learning_passed,
            criterion=(
                "The byte-identical new-thread query changes from a non-token baseline to the "
                "exact result-grounded token after receipt ten, and the selected revision exactly "
                "matches the terminal-review operation target and its evidence lineage."
            ),
            actual={
                "baseline_response": baseline,
                "after_response": after,
                "query_digests_equal": (
                    learning.baseline_query_sha256 == learning.after_query_sha256
                ),
                "selected_source_revisions": list(learning.selected_source_revisions),
                "selected_skill_revisions": list(learning.selected_skill_revisions),
                "selected_skill_source_revisions": list(learning.selected_skill_source_revisions),
                "applied_operation_ids": list(learning.applied_operation_ids),
                "pre_terminal_receipt_count": learning.pre_terminal_receipt_count,
                "head_changed_after_terminal_seal": (learning.head_changed_after_terminal_seal),
                "experience_source_binding_count": learning.experience_source_binding_count,
                "selected_operation_target_count": learning.selected_operation_target_count,
            },
        ),
        "clear-correction": _case(
            passed=clear_passed,
            criterion=(
                "A distinct authenticated class-level correction changes the byte-identical "
                "new-thread query to its exact source token through a source-linked operation."
            ),
            actual={
                "baseline_response": clear_correction.baseline_response,
                "after_response": clear_correction.after_response,
                "query_digests_equal": (
                    clear_correction.baseline_query_sha256 == clear_correction.after_query_sha256
                ),
                "source_linked_operation_ids": list(clear_correction.source_linked_operation_ids),
            },
        ),
        "terminal-experiences": _case(
            passed=receipt_passed,
            criterion=(
                "Ten actual terminal foreground receipts are admitted transactionally with at "
                "eight completed read observations and two failed outcomes."
            ),
            actual={
                "receipt_outcomes": list(receipts.outcomes),
                "admitted_outcomes": list(receipts.admitted_outcomes),
                "curation_result_projection_count": (receipts.curation_result_projection_count),
                "canonical_evidence_binding_count": (receipts.canonical_evidence_binding_count),
            },
        ),
        "task-only-boundary": _case(
            passed=task_passed,
            criterion=(
                "A current authenticated foreground correction creates a task overlay selected "
                "in that task only, absent for a new member and from CORE and skill heads."
            ),
            actual={
                "overlay_applied": boundaries.task_overlay_applied,
                "same_task_selected": boundaries.same_task_selected,
                "new_member_selected": boundaries.new_member_selected,
                "same_task_response": boundaries.task_same_response,
                "new_member_response": boundaries.task_new_member_response,
                "overlay_in_core": boundaries.task_overlay_in_core,
                "overlay_in_skill_heads": boundaries.task_overlay_in_skill_heads,
            },
        ),
        "private-boundary": _case(
            passed=private_passed,
            criterion="A private correction changes neither workspace counters nor shared heads.",
            actual={
                "counter_delta": boundaries.private_counter_delta,
                "head_changed": boundaries.private_head_changed,
            },
        ),
        "builtin-protection": _case(
            passed=builtin_passed,
            criterion=(
                "Background learning leaves builtin and override heads unchanged; the separate "
                "host guard rejects an autonomous agent-created shadow."
            ),
            actual={
                "model_attempted": boundaries.builtin_model_attempted,
                "model_attempt_rejected": boundaries.builtin_model_attempt_rejected,
                "host_rejected": boundaries.builtin_host_rejected,
                "heads_changed": boundaries.builtin_heads_changed,
            },
        ),
        "conflict-unknown-send": _case(
            passed=conflict_passed,
            criterion=(
                "The dynamically observed same-scope question remains pending after one unknown "
                "delivery, is not retried, and creates no tool approval record."
            ),
            actual={
                "question_id": boundaries.conflict_question_id,
                "question_status": boundaries.conflict_status,
                "delivery_state": boundaries.conflict_delivery_state,
                "retry_count": boundaries.conflict_retry_count,
                "approval_records": boundaries.conflict_approval_records,
            },
        ),
    }


class EvidenceRecorder:
    """Mutable crash ledger persisted after every expensive or stateful boundary."""

    output: Path
    invocation: str
    model_id: str
    started_at: str

    def __init__(self, *, output: Path, invocation: str, model_id: str, started_at: str) -> None:
        self.output = output
        self.invocation = invocation
        self.model_id = model_id
        self.started_at = started_at
        self.foreground_calls: list[JsonObject] = []
        self.curation_calls: list[JsonObject] = []
        self.search_calls: list[JsonObject] = []
        self.slack_sends: list[JsonObject] = []
        self.trials: list[JsonObject] = []
        self.snapshots: list[JsonObject] = []

    def checkpoint(self, phase: str, *, error: str | None = None) -> None:
        payload = _JSON_OBJECT.validate_python(
            {
                "schema": "trace.learning-model-canary-evidence.v1",
                "status": "running" if error is None else "failed",
                "phase": phase,
                "error": error,
                "invocation": self.invocation,
                "model_id": self.model_id,
                "started_at": self.started_at,
                "installed_package": str(Path(sys.modules["ads_booster"].__file__ or "").resolve()),
                "foreground_calls": self.foreground_calls,
                "curation_calls": self.curation_calls,
                "search_calls": self.search_calls,
                "slack_sends": self.slack_sends,
                "trials": self.trials,
                "snapshots": self.snapshots,
            }
        )
        _ = self.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


@dataclass(frozen=True, slots=True)
class ObservedReasoning:
    """Record every official foreground judgment without changing its request or result."""

    delegate: ReasoningProvider
    recorder: EvidenceRecorder
    max_calls: int | None = None

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        if self.max_calls is not None and len(self.recorder.foreground_calls) >= self.max_calls:
            raise CanaryHarnessError("minimal_model_call_limit_reached")
        started = time.monotonic()
        try:
            result = self.delegate.plan(request)
        except CodexReasoningError as error:
            self.recorder.foreground_calls.append(
                {
                    "request": request.model_dump(mode="json"),
                    "request_sha256": contract_sha256(request),
                    "status": "provider_error",
                    "error": str(error),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }
            )
            self.recorder.checkpoint("foreground-provider-error", error=str(error))
            raise
        self.recorder.foreground_calls.append(
            {
                "request": request.model_dump(mode="json"),
                "request_sha256": contract_sha256(request),
                "status": "succeeded",
                "result": result.model_dump(mode="json"),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        )
        self.recorder.checkpoint("foreground-call-recorded")
        return result


@dataclass(frozen=True, slots=True)
class SyntheticSearchExecutor:
    """Return production-shaped no-effect observations for model-selected searches."""

    recorder: EvidenceRecorder

    def __call__(
        self,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
    ) -> DelegatedToolResult:
        request = SearchInput.model_validate(invocation.input)
        failed = request.query.startswith("canary-failure-")
        output: JsonObject = {
            "fixture": "synthetic-procedure-evidence",
            "query": request.query,
            "procedure_code": _FAILED_TERMINAL_TOKEN if failed else _TERMINAL_TOKEN,
            "check_outcome": "failed" if failed else "completed",
            "error_code": "synthetic_source_unavailable" if failed else None,
        }
        self.recorder.search_calls.append(
            {
                "run_id": invocation.run_id,
                "invocation_id": invocation.invocation_id,
                "descriptor_sha256": contract_sha256(descriptor),
                "query": request.query,
                "disposition": "failed" if failed else "no_effect",
                "output": output,
            }
        )
        self.recorder.checkpoint("synthetic-search-receipt")
        return DelegatedToolResult(
            disposition="failed" if failed else "no_effect",
            output=output,
            actual_cost_units=1,
        )


class CapturingSender:
    """Persist all Slack intents locally and make only question delivery uncertain on demand."""

    recorder: EvidenceRecorder
    fail_questions: bool
    sequence: int

    def __init__(self, recorder: EvidenceRecorder) -> None:
        self.recorder = recorder
        self.fail_questions = False
        self.sequence = 0

    def send(self, payload: JsonObject) -> JsonObject:
        self.sequence += 1
        text = payload.get("text")
        is_question = isinstance(text, str) and "question." in text and "확인이 필요합니다" in text
        self.recorder.slack_sends.append(
            {
                "sequence": self.sequence,
                "payload": payload,
                "is_learning_question": is_question,
                "simulated_outcome": "unknown"
                if self.fail_questions and is_question
                else "delivered",
            }
        )
        self.recorder.checkpoint("slack-intent-captured")
        if self.fail_questions and is_question:
            raise TimeoutError("synthetic_question_delivery_unknown")
        return {"ok": True, "ts": f"fixture.{self.sequence:06d}"}


@dataclass(frozen=True, slots=True)
class Surface:
    installed: InstalledKnowledgeRuntime
    service: MarketingAgentService
    events: SlackEvents
    api: MarketingAgentApi

    def close(self) -> None:
        self.installed.runtime.close()


@dataclass(frozen=True, slots=True)
class Trial:
    name: str
    tenant_id: str
    run_id: str
    response: str
    reasoning_calls: tuple[JsonObject, ...]
    records: tuple[JsonObject, ...]


class Arguments(argparse.Namespace):
    scenario: str = ""
    home: Path = Path()
    output: Path = Path()


class CanarySemanticError(RuntimeError):
    """Raised after the complete evidence artifact records one or more failed cases."""


@dataclass(frozen=True, slots=True)
class CanaryHarnessError(RuntimeError):
    code: str
    detail: str | None = None

    @override
    def __str__(self) -> str:
        return self.code if self.detail is None else f"{self.code}:{self.detail}"


@contextmanager
def _loopback(api: MarketingAgentApi) -> Generator[str]:
    """Run the installed HTTP router on one captured ephemeral loopback port."""
    started = Event()
    captured: list[ThreadingHTTPServer] = []
    original = ThreadingHTTPServer

    class CapturingServer(original):
        def __init__(
            self,
            server_address: tuple[str | bytes | bytearray, int],
            request_handler_class: type[BaseHTTPRequestHandler],
            bind_and_activate: bool = True,
        ) -> None:
            super().__init__(server_address, request_handler_class, bind_and_activate)
            captured.append(self)
            started.set()

    with patch.object(http_api, "ThreadingHTTPServer", CapturingServer):
        thread = Thread(
            target=serve_marketing_agent_api,
            kwargs={"api": api, "host": "127.0.0.1", "port": 0},
            daemon=True,
        )
        thread.start()
        if not started.wait(5):
            raise CanaryHarnessError("model_canary_loopback_not_started")
        server = captured[0]
        host, port = server.server_address[:2]
        try:
            yield f"http://{host}:{port}"
        finally:
            server.shutdown()
            thread.join(timeout=5)


def _event(
    *,
    event_id: str,
    user: str,
    channel: str,
    text: str,
    timestamp: str,
    private: bool = False,
    thread_timestamp: str = "",
) -> bytes:
    return json.dumps(
        {
            "type": "event_callback",
            "api_app_id": "A1",
            "team_id": "T1",
            "event_id": event_id,
            "event": {
                "type": "message" if private or thread_timestamp else "app_mention",
                "channel": channel,
                "user": user,
                "text": text if private or thread_timestamp else f"<@UBOT> {text}",
                "ts": timestamp,
                "channel_type": "im" if private else "channel",
                **({"thread_ts": thread_timestamp} if thread_timestamp else {}),
            },
        },
        separators=(",", ":"),
    ).encode()


def _post_signed(base_url: str, body: bytes) -> JsonObject:
    timestamp = str(int(datetime.now(UTC).timestamp()))
    signature = (
        "v0="
        + hmac.new(
            b"fixture-signing",
            b"v0:" + timestamp.encode() + b":" + body,
            hashlib.sha256,
        ).hexdigest()
    )
    with create_http_client(read_timeout=15) as client:
        response = client.post_json(
            f"{base_url}/channels/slack/events",
            json.loads(body),
            {
                "x-slack-request-timestamp": timestamp,
                "x-slack-signature": signature,
            },
        )
    if response.status_code != 200:
        raise CanaryHarnessError("model_canary_signed_ingress_status", str(response.status_code))
    return response.json_object()


def _work_slack(events: SlackEvents) -> None:
    for _ in range(100):
        if not events.work_once(now=datetime.now(UTC)):
            return
    raise CanaryHarnessError("model_canary_slack_drain_limit")


def _assert_paths(home: Path, output: Path) -> None:
    if not home.is_absolute() or not output.is_absolute():
        raise CanaryHarnessError("model_canary_paths_must_be_absolute")
    if home.exists():
        raise CanaryHarnessError("model_canary_home_must_not_exist")
    if output.exists() or output.with_suffix(".rubric.json").exists():
        raise CanaryHarnessError("model_canary_output_must_not_exist")
    if os.environ.get("PYTHONPATH"):
        raise CanaryHarnessError("model_canary_pythonpath_must_be_empty")
    package_path = sys.modules["ads_booster"].__file__
    if package_path is None:
        raise CanaryHarnessError("model_canary_installed_package_missing")
    package_file = Path(package_path).resolve()
    checkout = Path(__file__).resolve().parents[2]
    cwd = Path.cwd().resolve()
    if checkout == cwd or checkout in cwd.parents:
        raise CanaryHarnessError("model_canary_cwd_inside_checkout")
    if checkout in package_file.parents:
        raise CanaryHarnessError("model_canary_imported_from_checkout")


def _invocation() -> str:
    return (
        "cd "
        + shlex.quote(str(Path.cwd()))
        + " && env -u PYTHONPATH "
        + shlex.join([str(Path(sys.executable)), "-I", *sys.argv])
    )


def _build_surface(
    *,
    settings: KnowledgeSettings,
    paths: InstalledServicePaths,
    codex: CodexCli,
    model_id: str,
    installation: Path,
    recorder: EvidenceRecorder,
    sender: CapturingSender,
    search: SyntheticSearchExecutor,
    max_foreground_calls: int | None = None,
    allowed_capability_ids: tuple[str, ...] | None = None,
) -> Surface:
    installed = build_installed_knowledge_runtime(
        settings=settings,
        service_database=paths.database,
        codex=codex,
        model_id=model_id,
    )
    base = build_installed_marketing_agent_service(
        paths=paths,
        codex_executable=codex.executable,
        model_id=model_id,
        timeout_seconds=180,
        knowledge=installed.adapter,
    )
    service = base
    service.reasoning = ObservedReasoning(
        base.reasoning,
        recorder,
        max_calls=max_foreground_calls,
    )
    if allowed_capability_ids is not None:
        service.capability_policy = CapabilityPolicy(allowed_capability_ids=allowed_capability_ids)
    service.tools = {
        **service.tools,
        "research.search": DelegatingToolAdapter(
            capability_id="research.search",
            version="1",
            executor_id="synthetic.learning.canary",
            executor=search,
        ),
    }
    commands = slack_from_env(
        {
            "TRACE_MARKETING_SLACK_INSTALLATION": str(installation),
            "TRACE_MARKETING_SLACK_SIGNING_SECRET": "fixture-signing",
            "TRACE_MARKETING_SLACK_BOT_TOKEN": "fixture-never-used",
            "TRACE_MARKETING_SLACK_CHANNEL_ID": "C1",
            "TRACE_MARKETING_PUBLIC_ORIGIN": "https://fixture.invalid",
        },
        service,
        tenant_id="trace",
    )
    if commands is None:
        installed.runtime.close()
        raise CanaryHarnessError("model_canary_slack_commands_missing")
    commands.sender = sender.send
    events = SlackEvents(commands, "UBOT", frozenset({"C1"}))
    return Surface(
        installed=installed,
        service=service,
        events=events,
        api=MarketingAgentApi(
            service=service,
            tenant_id="trace",
            principal_id=load_local_actor(settings).actor_id,
            bearer_token="fixture-token",
            slack_events=events,
            knowledge_ingress=installed.adapter.ingress,
        ),
    )


def _trial_response(calls: tuple[JsonObject, ...]) -> str:
    for call in reversed(calls):
        result = call.get("result")
        if not isinstance(result, dict):
            continue
        decision = result.get("decision")
        if isinstance(decision, dict) and decision.get("action") == "stop":
            summary = decision.get("reasoning_summary")
            if isinstance(summary, str):
                return summary
    return ""


def _conversation_run(surface: Surface, channel: str, root_timestamp: str) -> tuple[str, str]:
    conversation = next(
        (
            item
            for item in surface.events.store.conversations()
            if item.channel_id == channel and item.thread_ts == root_timestamp
        ),
        None,
    )
    if conversation is None or not conversation.current_run:
        raise CanaryHarnessError("model_canary_conversation_run_missing")
    return conversation.tenant_id, conversation.current_run


def _submit(
    *,
    surface: Surface,
    base_url: str,
    recorder: EvidenceRecorder,
    name: str,
    event_id: str,
    user: str,
    channel: str,
    text: str,
    timestamp: str,
    private: bool = False,
    thread_timestamp: str = "",
    project_questions: bool = True,
    run_learning: bool = True,
) -> Trial:
    before_calls = len(recorder.foreground_calls)
    acknowledged = _post_signed(
        base_url,
        _event(
            event_id=event_id,
            user=user,
            channel=channel,
            text=text,
            timestamp=timestamp,
            private=private,
            thread_timestamp=thread_timestamp,
        ),
    )
    if acknowledged != {"ok": True}:
        raise CanaryHarnessError("model_canary_signed_ingress_not_acknowledged")
    _work_slack(surface.events)
    if run_learning:
        surface.installed.runtime.run_until_idle(flush_batches=False)
    if project_questions and run_learning:
        _work_slack(surface.events)
    root_timestamp = "" if private else (thread_timestamp or timestamp)
    tenant_id, run_id = _conversation_run(surface, channel, root_timestamp)
    calls = tuple(
        call
        for call in recorder.foreground_calls[before_calls:]
        if _foreground_run_id(call) == run_id
    )
    records = tuple(
        record.model_dump(mode="json")
        for record in surface.service.repository.records(tenant_id, run_id)
    )
    trial = Trial(
        name=name,
        tenant_id=tenant_id,
        run_id=run_id,
        response=_trial_response(calls),
        reasoning_calls=calls,
        records=records,
    )
    recorder.trials.append(
        {
            "name": trial.name,
            "event_id": event_id,
            "user": user,
            "channel": channel,
            "thread_timestamp": root_timestamp,
            "private": private,
            "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "run_id": trial.run_id,
            "tenant_id": trial.tenant_id,
            "response": trial.response,
            "reasoning_calls": list(trial.reasoning_calls),
            "records": list(trial.records),
        }
    )
    recorder.checkpoint(f"trial:{name}")
    return trial


def _foreground_run_id(call: JsonObject) -> str | None:
    request = call.get("request")
    if not isinstance(request, dict):
        return None
    run_id = request.get("run_id")
    return run_id if isinstance(run_id, str) else None


def _revision_key(value: JsonValue, *, owner: str) -> str | None:
    if not isinstance(value, dict):
        return None
    owner_id = value.get(owner)
    revision_id = value.get("revision_id")
    if not isinstance(owner_id, str) or not isinstance(revision_id, str):
        return None
    return f"{owner_id}@{revision_id}"


def _revision_keys(value: JsonValue, *, owner: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    keys: list[str] = []
    for item in value:
        key = _revision_key(item, owner=owner)
        if key is not None:
            keys.append(key)
    return tuple(keys)


def _skill_source_revision_keys(value: JsonValue) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    keys: list[str] = []
    for skill in value:
        if not isinstance(skill, dict):
            continue
        keys.extend(_revision_keys(skill.get("source_revisions"), owner="source_id"))
    return tuple(keys)


def _prepared_revisions(
    trial: Trial,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    for call in reversed(trial.reasoning_calls):
        request = call.get("request")
        if not isinstance(request, dict):
            continue
        prepared = request.get("prepared_context")
        if not isinstance(prepared, dict):
            continue
        receipt = prepared.get("receipt")
        if not isinstance(receipt, dict):
            continue
        source = receipt.get("selected_source_revisions")
        skills = receipt.get("selected_skill_revisions")
        selected_sources = _revision_keys(source, owner="source_id")
        selected_skills = _revision_keys(skills, owner="skill_id")
        skill_sources = _skill_source_revision_keys(skills)
        return selected_sources, selected_skills, skill_sources
    return (), (), ()


def _selected_task_overlay(trial: Trial) -> bool:
    for call in trial.reasoning_calls:
        request = call.get("request")
        if not isinstance(request, dict):
            continue
        prepared = request.get("prepared_context")
        if not isinstance(prepared, dict):
            continue
        blocks = prepared.get("blocks")
        if isinstance(blocks, list) and any(
            isinstance(block, dict) and block.get("slot") == "task_overlay" for block in blocks
        ):
            return True
    return False


def _state_snapshot(surface: Surface, settings: KnowledgeSettings, label: str) -> JsonObject:
    actor = load_local_actor(settings)
    repository = surface.installed.adapter.repository
    core = repository.read_memory(actor, "memory.core")
    skills = {
        skill_id: (
            None
            if (stored := repository.read_skill(actor, skill_id)) is None
            else {
                "version": stored.record.version,
                "digest": stored.record.digest,
                "record": stored.record.model_dump(mode="json"),
                "body_sha256": hashlib.sha256(stored.body).hexdigest(),
            }
        )
        for skill_id in repository.skill_ids(actor)
    }
    rounds = LearningReviewCoordinator(repository).rounds(actor.workspace_id)
    snapshot = _JSON_OBJECT.validate_python(
        {
            "label": label,
            "counter": LearningReviewCoordinator(repository)
            .counter(actor.workspace_id)
            .model_dump(mode="json"),
            "rounds": [item.model_dump(mode="json") for item in rounds],
            "core": None
            if core is None
            else {
                "revision": core.revision.model_dump(mode="json"),
                "entries": [entry.model_dump(mode="json") for entry in core.entries],
                "body_sha256": hashlib.sha256(core.body).hexdigest(),
            },
            "skill_heads": skills,
            "builtin_catalog": [
                {
                    "skill_id": item.skill_id,
                    "version": item.version,
                    "digest": contract_sha256(
                        {
                            "skill_id": item.skill_id,
                            "version": item.version,
                            "purpose": item.purpose,
                            "procedure": item.procedure,
                        }
                    ),
                }
                for item in SKILLS
            ],
        }
    )
    snapshot["state_sha256"] = contract_sha256(snapshot)
    return snapshot


def _applied_operations(surface: Surface) -> tuple[str, ...]:
    with surface.installed.adapter.repository.connection() as database:
        rows = database.execute(
            "SELECT operation_id FROM operations WHERE status='applied' ORDER BY rowid"
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _receipt_outcome(receipt: ToolReceiptRecord) -> ReceiptOutcome | None:
    match receipt.disposition:
        case "succeeded":
            return "succeeded"
        case "failed":
            return "failed"
        case "no_effect":
            return "observed"
        case "unknown_side_effect":
            return None
        case unreachable:
            assert_never(unreachable)


def _synthetic_receipt_evidence(
    surface: Surface, run_ids: tuple[str, ...]
) -> tuple[JsonObject, ...]:
    evidence: list[JsonObject] = []
    for run_id in run_ids:
        for record in surface.service.repository.records("trace", run_id):
            if record.kind is not AgentRecordKind.RECEIPT:
                continue
            receipt = ToolReceiptRecord.model_validate(record.payload)
            if receipt.executor_id != "synthetic.learning.canary":
                continue
            outcome = _receipt_outcome(receipt)
            evidence.append(
                {
                    "run_id": run_id,
                    "receipt_id": receipt.receipt_id,
                    "invocation_sha256": receipt.invocation_sha256,
                    "executor_id": receipt.executor_id,
                    "raw_disposition": receipt.disposition,
                    "learning_outcome": outcome,
                    "output_sha256": receipt.output_sha256,
                }
            )
    return tuple(evidence)


def _receipt_outcomes(evidence: tuple[JsonObject, ...]) -> tuple[ReceiptOutcome, ...]:
    outcomes: list[ReceiptOutcome] = []
    for item in evidence:
        outcome = item.get("learning_outcome")
        match outcome:
            case "observed" | "succeeded" | "failed" as parsed:
                outcomes.append(parsed)
            case bool() | int() | float() | str() | list() | dict() | None:
                continue
            case unreachable:
                assert_never(unreachable)
    return tuple(outcomes)


def _admitted_receipts(surface: Surface, run_ids: tuple[str, ...]) -> tuple[ReceiptOutcome, ...]:
    coordinator = LearningReviewCoordinator(surface.installed.adapter.repository)
    outcomes: list[ReceiptOutcome] = []
    for run_id in run_ids:
        for experience in coordinator.experiences_for_run(run_id):
            if experience.capability_id != "research.search":
                continue
            match experience.outcome.value:
                case "observed" | "succeeded" | "failed" as outcome:
                    outcomes.append(outcome)
                case "unknown_side_effect" | "invalidated":
                    continue
                case unreachable:
                    assert_never(unreachable)
    return tuple(outcomes)


def _contains_mapping(value: JsonValue, expected: JsonObject) -> bool:
    match value:
        case dict() as item:
            if all(item.get(key) == expected_value for key, expected_value in expected.items()):
                return True
            return any(_contains_mapping(nested, expected) for nested in item.values())
        case list() as items:
            return any(_contains_mapping(item, expected) for item in items)
        case str() as text:
            try:
                decoded = _JSON_VALUE.validate_python(json.loads(text))
            except json.JSONDecodeError:
                return False
            return _contains_mapping(decoded, expected)
        case bool() | int() | float() | None:
            return False
        case unreachable:
            assert_never(unreachable)


def _projected_search_results(recorder: EvidenceRecorder, run_ids: tuple[str, ...]) -> int:
    projections = 0
    curation: JsonValue = _JSON_OBJECT.validate_python({"calls": recorder.curation_calls})
    for call in recorder.search_calls:
        if call.get("run_id") not in run_ids:
            continue
        output = call.get("output")
        if isinstance(output, dict) and _contains_mapping(curation, output):
            projections += 1
    return projections


def _tool_output_record(
    records: tuple[JsonObject, ...], receipt: ToolReceiptRecord
) -> JsonObject | None:
    receipt_sha256 = contract_sha256(receipt)
    for record in records:
        if record.get("kind") != AgentRecordKind.EVIDENCE.value:
            continue
        payload = record.get("payload")
        if (
            isinstance(payload, dict)
            and payload.get("schema_version") == "trace.tool-output-evidence.v1"
            and payload.get("receipt_sha256") == receipt_sha256
        ):
            return payload
    return None


def _experience_lineage(
    surface: Surface,
    recorder: EvidenceRecorder,
    run_ids: tuple[str, ...],
) -> tuple[JsonObject, ...]:
    curation_projection: JsonValue = _JSON_OBJECT.validate_python(
        {"calls": recorder.curation_calls}
    )
    coordinator = LearningReviewCoordinator(surface.installed.adapter.repository)
    lineage: list[JsonObject] = []
    for run_id in run_ids:
        learning_source = surface.installed.adapter.ingress.source_for_run(run_id)
        stored_source = (
            None
            if learning_source is None
            else surface.installed.adapter.repository.read_source(
                learning_source.binding.actor,
                learning_source.receipt.source_id,
            )
        )
        records = tuple(
            record.model_dump(mode="json")
            for record in surface.service.repository.records("trace", run_id)
        )
        invocations = tuple(
            ToolInvocation.model_validate(record["payload"])
            for record in records
            if record.get("kind") == AgentRecordKind.INVOCATION.value
            and isinstance(record.get("payload"), dict)
        )
        receipts = tuple(
            ToolReceiptRecord.model_validate(record["payload"])
            for record in records
            if record.get("kind") == AgentRecordKind.RECEIPT.value
            and isinstance(record.get("payload"), dict)
        )
        for experience in coordinator.experiences_for_run(run_id):
            if experience.capability_id != "research.search":
                continue
            invocation = next(
                (item for item in invocations if item.invocation_id == experience.invocation_id),
                None,
            )
            receipt = next(
                (item for item in receipts if item.receipt_id == experience.receipt_id),
                None,
            )
            evidence = experience.evidence
            output_record = None if receipt is None else _tool_output_record(records, receipt)
            output = None if output_record is None else output_record.get("output")
            input_digest_matches = (
                evidence is not None
                and invocation is not None
                and evidence.input_sha256 == contract_sha256(invocation.input)
            )
            output_digest_matches = (
                evidence is not None
                and receipt is not None
                and isinstance(output, dict)
                and evidence.output_sha256 == receipt.output_sha256
                and evidence.output_sha256 == contract_sha256(output)
            )
            complete = (
                evidence is not None
                and evidence.input_completeness.value == "complete"
                and evidence.output_completeness.value == "complete"
            )
            projected = _contains_mapping(curation_projection, experience.model_dump(mode="json"))
            lineage.append(
                {
                    "run_id": run_id,
                    "experience_id": experience.experience_id,
                    "receipt_id": experience.receipt_id,
                    "invocation_id": experience.invocation_id,
                    "outcome": experience.outcome.value,
                    "source_event_id": (
                        None if learning_source is None else learning_source.event.message_id
                    ),
                    "source_id": (
                        None if learning_source is None else learning_source.receipt.source_id
                    ),
                    "source_revision_id": (
                        None
                        if learning_source is None
                        else learning_source.receipt.source_revision_id
                    ),
                    "source_content_sha256": (
                        None if stored_source is None else stored_source.source.sha256
                    ),
                    "evidence": None if evidence is None else evidence.model_dump(mode="json"),
                    "canonical_invocation_input": (
                        None if invocation is None else invocation.input
                    ),
                    "canonical_tool_output_record": output_record,
                    "input_digest_matches": input_digest_matches,
                    "output_digest_matches": output_digest_matches,
                    "complete": complete,
                    "projected_to_curation": projected,
                    "binding_valid": (
                        input_digest_matches and output_digest_matches and complete and projected
                    ),
                }
            )
    return tuple(lineage)


def _experience_bound_operations(
    recorder: EvidenceRecorder,
    operation_ids: tuple[str, ...],
    experience_ids: frozenset[str],
) -> tuple[str, ...]:
    terminal_calls = tuple(
        call
        for call in recorder.curation_calls
        if _terminal_call_experience_ids(call) == experience_ids
    )
    return tuple(
        operation_id
        for operation_id in operation_ids
        if any(
            operation_id
            in json.dumps(call.get("result"), ensure_ascii=False, separators=(",", ":"))
            for call in terminal_calls
        )
    )


def _terminal_call_experience_ids(call: JsonObject) -> frozenset[str]:
    jobs = call.get("jobs")
    if not isinstance(jobs, list):
        return frozenset()
    experience_ids: set[str] = set()
    terminal = False
    for job in jobs:
        if not isinstance(job, dict):
            continue
        request = job.get("request")
        if not isinstance(request, dict):
            continue
        if request.get("learning_purpose") != "terminal_experience_review":
            continue
        terminal = True
        review = request.get("learning_review")
        if not isinstance(review, dict):
            continue
        experiences = review.get("experience_refs")
        if not isinstance(experiences, list):
            continue
        for experience in experiences:
            if not isinstance(experience, dict):
                continue
            experience_id = experience.get("experience_id")
            if isinstance(experience_id, str):
                experience_ids.add(experience_id)
    return frozenset(experience_ids) if terminal else frozenset()


def _operation_targets(surface: Surface, operation_ids: tuple[str, ...]) -> tuple[JsonObject, ...]:
    targets: list[JsonObject] = []
    with surface.installed.adapter.repository.connection() as database:
        for operation_id in operation_ids:
            rows = database.execute(
                """SELECT record_kind,record_json FROM operation_records
                WHERE operation_id=? ORDER BY ordinal""",
                (operation_id,),
            ).fetchall()
            for record_kind, record_json in rows:
                if str(record_kind) == "SkillOperation":
                    operation = SkillOperation.model_validate_json(str(record_json))
                    record = operation.record
                    if record is None:
                        continue
                    targets.append(
                        {
                            "operation_id": operation_id,
                            "target_kind": "skill",
                            "target_id": record.skill_id,
                            "revision_id": record.version,
                            "content_sha256": record.digest,
                            "source_refs": [
                                item.model_dump(mode="json") for item in record.source_refs
                            ],
                        }
                    )
                    continue
                if str(record_kind) != "MemoryOperation":
                    continue
                operation = MemoryOperation.model_validate_json(str(record_json))
                head = database.execute(
                    """SELECT resulting_revision_id FROM operation_heads
                    WHERE operation_id=? AND entity_kind='memory' AND entity_id=?""",
                    (operation_id, operation.document_id),
                ).fetchone()
                if head is None:
                    continue
                revision_id = str(head[0])
                revision = database.execute(
                    """SELECT body_sha256 FROM memory_revisions
                    WHERE workspace_id=? AND document_id=? AND revision_id=?""",
                    ("trace", operation.document_id, revision_id),
                ).fetchone()
                if revision is None:
                    continue
                targets.append(
                    {
                        "operation_id": operation_id,
                        "target_kind": "memory",
                        "target_id": operation.document_id,
                        "revision_id": revision_id,
                        "content_sha256": str(revision[0]),
                        "source_refs": list(operation.evidence_refs),
                    }
                )
    return tuple(targets)


def _source_bound_operations(
    surface: Surface, operation_ids: tuple[str, ...], run_id: str
) -> tuple[str, ...]:
    source = surface.installed.adapter.ingress.source_for_run(run_id)
    if source is None:
        return ()
    expected_ids = {source.event.message_id, source.receipt.source_id}
    bound: list[str] = []
    with surface.installed.adapter.repository.connection() as database:
        for operation_id in operation_ids:
            rows = database.execute(
                "SELECT record_json FROM operation_records WHERE operation_id=? ORDER BY ordinal",
                (operation_id,),
            ).fetchall()
            if any(
                any(expected in str(record_json) for expected in expected_ids)
                for (record_json,) in rows
            ):
                bound.append(operation_id)
    return tuple(bound)


def _selected_targets(trial: Trial) -> tuple[JsonObject, ...]:
    for call in reversed(trial.reasoning_calls):
        request = call.get("request")
        if not isinstance(request, dict):
            continue
        prepared = request.get("prepared_context")
        if not isinstance(prepared, dict):
            continue
        receipt = prepared.get("receipt")
        if not isinstance(receipt, dict):
            continue
        targets: list[JsonObject] = []
        for field_name, target_kind, id_name in (
            ("selected_skill_revisions", "skill", "skill_id"),
            ("selected_memory_revisions", "memory", "document_id"),
        ):
            values = receipt.get(field_name)
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, dict):
                    continue
                target_id = value.get(id_name)
                revision_id = value.get("revision_id")
                content_sha256 = value.get("content_sha256")
                if not all(
                    isinstance(item, str) for item in (target_id, revision_id, content_sha256)
                ):
                    continue
                targets.append(
                    {
                        "target_kind": target_kind,
                        "target_id": target_id,
                        "revision_id": revision_id,
                        "content_sha256": content_sha256,
                        "source_revisions": value.get("source_revisions", []),
                    }
                )
        return tuple(targets)
    return ()


def _matching_selected_targets(
    operation_targets: tuple[JsonObject, ...],
    selected_targets: tuple[JsonObject, ...],
    experience_lineage: tuple[JsonObject, ...],
) -> tuple[JsonObject, ...]:
    matches: list[JsonObject] = []
    for target in operation_targets:
        selected = next(
            (
                item
                for item in selected_targets
                if (
                    item.get("target_kind"),
                    item.get("target_id"),
                    item.get("revision_id"),
                    item.get("content_sha256"),
                )
                == (
                    target.get("target_kind"),
                    target.get("target_id"),
                    target.get("revision_id"),
                    target.get("content_sha256"),
                )
            ),
            None,
        )
        if selected is None:
            continue
        if target.get("target_kind") == "skill" and not _skill_source_lineage_valid(
            target, selected, experience_lineage
        ):
            continue
        matches.append(target)
    return tuple(matches)


def _skill_source_lineage_valid(
    target: JsonObject,
    selected: JsonObject,
    experience_lineage: tuple[JsonObject, ...],
) -> bool:
    source_refs = target.get("source_refs")
    selected_revisions = selected.get("source_revisions")
    if not isinstance(source_refs, list) or not isinstance(selected_revisions, list):
        return False
    reference_ids: set[str] = set()
    for reference in source_refs:
        if not isinstance(reference, dict):
            continue
        evidence_id = reference.get("evidence_id")
        if isinstance(evidence_id, str):
            reference_ids.add(evidence_id)
    bound_lineage = tuple(
        item
        for item in experience_lineage
        if item.get("source_event_id") in reference_ids or item.get("source_id") in reference_ids
    )
    if not bound_lineage:
        return False
    selected_keys: set[tuple[str, str, str]] = set()
    for item in selected_revisions:
        if not isinstance(item, dict):
            continue
        source_id = item.get("source_id")
        revision_id = item.get("revision_id")
        content_sha256 = item.get("content_sha256")
        if (
            isinstance(source_id, str)
            and isinstance(revision_id, str)
            and isinstance(content_sha256, str)
        ):
            selected_keys.add((source_id, revision_id, content_sha256))
    return all(
        (
            item.get("source_id"),
            item.get("source_revision_id"),
            item.get("source_content_sha256"),
        )
        in selected_keys
        for item in bound_lineage
    )


def _task_overlay_applied(trial: Trial) -> bool:
    for record in trial.records:
        if record.get("kind") != AgentRecordKind.EVIDENCE.value:
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict) or payload.get("schema_version") != (
            "trace.tool-output-evidence.v1"
        ):
            continue
        output = payload.get("output")
        if not isinstance(output, dict):
            continue
        data = output.get("data")
        if (
            output.get("status") in {"applied", "replayed"}
            and isinstance(data, dict)
            and data.get("status") == "task_only"
        ):
            return True
    return False


def _contains_token(snapshot: JsonObject, token: str, section: str) -> bool:
    value = snapshot.get(section)
    return token in json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _questions(surface: Surface) -> tuple[QuestionRecord, ...]:
    with surface.installed.adapter.repository.connection() as database:
        rows = database.execute(
            "SELECT question_json FROM knowledge_questions ORDER BY created_at,question_id"
        ).fetchall()
    return tuple(QuestionRecord.model_validate_json(str(row[0])) for row in rows)


def _question_delivery(surface: Surface, question_id: str) -> str | None:
    with surface.events.store.connect() as database:
        row = database.execute(
            """SELECT notification_state FROM slack_message_jobs
            WHERE json_extract(message_json,'$.learning_question_intent.question_id')=?
            ORDER BY rowid DESC LIMIT 1""",
            (question_id,),
        ).fetchone()
    return None if row is None else str(row[0])


def _approval_records(surface: Surface, run_id: str) -> int:
    return sum(
        record.kind is AgentRecordKind.APPROVAL
        for record in surface.service.repository.records("trace", run_id)
    )


def _source_ref(surface: Surface, run_id: str) -> tuple[EvidenceRef, str]:
    source = surface.installed.adapter.ingress.source_for_run(run_id)
    if source is None:
        raise CanaryHarnessError("model_canary_source_missing")
    event = source.event
    return (
        EvidenceRef(
            evidence_kind=EvidenceKind.CONVERSATION_EVENT,
            evidence_id=event.message_id,
            revision_id=str(event.revision),
            quote_sha256=hashlib.sha256(event.text.encode()).hexdigest(),
            scope=event.scope,
            instruction_authority=InstructionAuthority.AUTHORIZED_USER,
            provenance=Provenance.HUMAN_DIRECT,
        ),
        source.binding.actor.actor_id,
    )


def _agent_skill_shadow(*, skill_id: str, source_ref: EvidenceRef, actor_id: str) -> SkillRecord:
    version = f"{skill_id}.autonomous-canary.r1"
    now = datetime.now(UTC)
    applicability = AppliesTo(action_kinds=(KnowledgeActionKind.TEAM_CHAT,))
    stable: JsonObject = {
        "skill_id": skill_id,
        "version": version,
        "description": "Autonomous background attempt to shadow an installed builtin.",
        "procedure": "Use only source-linked successful procedures.",
        "pitfalls": [],
        "verification": [],
        "required_capability_ids": [],
        "origin": SkillOrigin.AGENT_CREATED.value,
        "protected": False,
        "base_builtin_digest": None,
        "source_refs": [source_ref.model_dump(mode="json")],
        "applicability": applicability.model_dump(mode="json", exclude_defaults=True),
        "created_by": actor_id,
    }
    return SkillRecord(
        schema="knowledge.skill-record.v1",
        skill_id=skill_id,
        version=version,
        description=str(stable["description"]),
        procedure=str(stable["procedure"]),
        origin=SkillOrigin.AGENT_CREATED,
        protected=False,
        digest=contract_sha256(stable),
        applicability=applicability,
        source_refs=(source_ref,),
        created_by=actor_id,
        created_at=now,
        updated_at=now,
    )


def _builtin_guard(surface: Surface, settings: KnowledgeSettings, run_id: str) -> JsonObject:
    builtin = SKILLS[0]
    before = _builtin_snapshot(surface, settings)
    source_ref, actor_id = _source_ref(surface, run_id)
    context = surface.installed.adapter.resolve_invocation(run_id, "builtin-codeguard-canary")
    record = _agent_skill_shadow(
        skill_id=builtin.skill_id,
        source_ref=source_ref,
        actor_id=actor_id,
    )
    operation_id = "operation.canary.autonomous-builtin-shadow"
    request = SkillApplyInput(
        schema="knowledge.tool.skill-apply.v1",
        operation_id=operation_id,
        operations=(
            SkillOperation(
                operation_id=operation_id,
                kind=SkillOperationKind.CREATE,
                skill_id=record.skill_id,
                replacement_revision_id=record.version,
                record=record,
                source_refs=record.source_refs,
                reason="Autonomous background canary must not shadow an installed builtin.",
            ),
        ),
    )
    result = surface.installed.adapter.host.execute(
        "skill_apply", request.model_dump(mode="json", by_alias=True), context
    )
    after = _builtin_snapshot(surface, settings)
    return {
        "scope": "codeguard_only_not_model_semantics",
        "builtin_id": builtin.skill_id,
        "status": result.status.value,
        "error_code": result.error_code,
        "before": before,
        "after": after,
        "unchanged": before == after,
        "rejected": result.status is ToolResultStatus.REJECTED,
    }


def _builtin_snapshot(surface: Surface, settings: KnowledgeSettings) -> JsonObject:
    actor = load_local_actor(settings)
    stored_heads: JsonObject = {
        item.skill_id: surface.installed.adapter.repository.skill_head(actor, item.skill_id)
        for item in SKILLS
    }
    return {
        "catalog": [
            {
                "skill_id": item.skill_id,
                "version": item.version,
                "digest": contract_sha256(
                    {
                        "skill_id": item.skill_id,
                        "version": item.version,
                        "purpose": item.purpose,
                        "procedure": item.procedure,
                    }
                ),
            }
            for item in SKILLS
        ],
        "stored_heads": stored_heads,
    }


def _head_digest(snapshot: JsonObject) -> str:
    return contract_sha256(
        {
            "core": snapshot.get("core"),
            "skill_heads": snapshot.get("skill_heads"),
            "builtin_catalog": snapshot.get("builtin_catalog"),
        }
    )


def _counter_total(snapshot: JsonObject) -> int:
    counter = snapshot.get("counter")
    if not isinstance(counter, dict):
        return -1
    turns = counter.get("conversation_turns")
    receipts = counter.get("terminal_tool_receipts")
    return (turns if isinstance(turns, int) else 0) + (receipts if isinstance(receipts, int) else 0)


def _model_builtin_behavior(recorder: EvidenceRecorder) -> tuple[bool, bool | None]:
    builtin_ids = {item.skill_id for item in SKILLS}
    attempted = False
    rejected = False
    for call in recorder.curation_calls:
        result = call.get("result")
        if isinstance(result, dict):
            decisions = result.get("decisions")
            if isinstance(decisions, list):
                for item in decisions:
                    if not isinstance(item, dict):
                        continue
                    decision = item.get("decision")
                    if not isinstance(decision, dict) or decision.get("tool_name") != "skill_apply":
                        continue
                    arguments = decision.get("tool_arguments_json")
                    if isinstance(arguments, str) and any(
                        builtin_id in arguments for builtin_id in builtin_ids
                    ):
                        attempted = True
        jobs = call.get("jobs")
        if isinstance(jobs, list):
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                observations = job.get("observations")
                if isinstance(observations, list) and any(
                    _observation_rejected(observation) for observation in observations
                ):
                    rejected = True
    return attempted, (rejected if attempted else None)


def _observation_rejected(observation: JsonValue) -> bool:
    if not isinstance(observation, dict):
        return False
    result = observation.get("result")
    return isinstance(result, dict) and result.get("status") == "rejected"


class BatchDecisionMethod(Protocol):
    def __call__(
        self,
        provider: CodexKnowledgeProvider,
        /,
        batch_id: str,
        jobs: tuple[CurationBatchJobContext, ...],
        *,
        timeout_seconds: float,
    ) -> CurationBatchDecision: ...


def _recording_batch(
    recorder: EvidenceRecorder,
    original_batch: BatchDecisionMethod,
) -> BatchDecisionMethod:
    def observed(
        provider: CodexKnowledgeProvider,
        batch_id: str,
        jobs: tuple[CurationBatchJobContext, ...],
        *,
        timeout_seconds: float,
    ) -> CurationBatchDecision:
        started = time.monotonic()
        entry: JsonObject = {
            "batch_id": batch_id,
            "jobs": [job.model_dump(mode="json") for job in jobs],
            "timeout_seconds": timeout_seconds,
            "model_id": provider.model_id,
        }
        try:
            result = original_batch(
                provider,
                batch_id,
                jobs,
                timeout_seconds=timeout_seconds,
            )
        except CodexKnowledgeError as error:
            entry["status"] = "provider_error"
            entry["error"] = str(error)
            entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
            recorder.curation_calls.append(entry)
            recorder.checkpoint("curation-provider-error", error=str(error))
            raise
        entry["status"] = "succeeded"
        entry["result"] = result.model_dump(mode="json")
        entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
        recorder.curation_calls.append(entry)
        recorder.checkpoint("curation-call-recorded")
        return result

    return observed


def _model_id(codex: CodexCli) -> str:
    if codex.model is None:
        raise CanaryHarnessError("model_canary_model_id_missing")
    return codex.model


def _initialize(home: Path) -> tuple[KnowledgeSettings, InstalledServicePaths, Path]:
    home.mkdir(mode=0o700, parents=True, exist_ok=False)
    settings = KnowledgeSettings(
        root=home / "knowledge",
        control_root=home / "knowledge-control",
        policy_path=home / "knowledge-control" / "policy.json",
    )
    _ = initialize_local_configuration(*settings.require_enabled(), workspace_id="trace")
    _ = initialize_knowledge_store(settings)
    paths = InstalledServicePaths(home / "service")
    paths.prepare()
    installation = home / "slack-installation.json"
    _ = installation.write_text(
        json.dumps(
            {
                "app_id": "A1",
                "team_id": "T1",
                "tenant_id": "trace",
                "members": [
                    {"slack_user_id": "U1", "member_id": "member-one"},
                    {"slack_user_id": "U2", "member_id": "member-two"},
                ],
            }
        )
    )
    return settings, paths, installation


def _tool_exchanges(trial: Trial, capability_id: str) -> tuple[JsonObject, ...]:
    invocations = tuple(
        ToolInvocation.model_validate(record["payload"])
        for record in trial.records
        if record.get("kind") == AgentRecordKind.INVOCATION.value
        and isinstance(record.get("payload"), dict)
    )
    receipts = tuple(
        ToolReceiptRecord.model_validate(record["payload"])
        for record in trial.records
        if record.get("kind") == AgentRecordKind.RECEIPT.value
        and isinstance(record.get("payload"), dict)
    )
    exchanges: list[JsonObject] = []
    for record in trial.records:
        if record.get("kind") != AgentRecordKind.EVIDENCE.value:
            continue
        evidence = record.get("payload")
        if not isinstance(evidence, dict) or evidence.get("capability_id") != capability_id:
            continue
        receipt_sha256 = evidence.get("receipt_sha256")
        receipt = next(
            (
                item
                for item in receipts
                if isinstance(receipt_sha256, str) and contract_sha256(item) == receipt_sha256
            ),
            None,
        )
        invocation = (
            None
            if receipt is None
            else next(
                (
                    item
                    for item in invocations
                    if contract_sha256(item) == receipt.invocation_sha256
                ),
                None,
            )
        )
        exchanges.append(
            {
                "capability_id": capability_id,
                "invocation": (None if invocation is None else invocation.model_dump(mode="json")),
                "receipt": None if receipt is None else receipt.model_dump(mode="json"),
                "output_evidence": evidence,
                "bindings_valid": receipt is not None and invocation is not None,
            }
        )
    return tuple(exchanges)


def _canonical_source_lineage(surface: Surface, run_id: str) -> JsonObject:
    source = surface.installed.adapter.ingress.source_for_run(run_id)
    if source is None:
        raise CanaryHarnessError("minimal_u1_source_missing")
    stored = surface.installed.adapter.repository.read_source(
        source.binding.actor,
        source.receipt.source_id,
    )
    if stored is None:
        raise CanaryHarnessError("minimal_u1_stored_source_missing")
    event = source.event
    return _JSON_OBJECT.validate_python(
        {
            "run_id": run_id,
            "event_id": event.message_id,
            "event_revision": str(event.revision),
            "event_text_sha256": hashlib.sha256(event.text.encode()).hexdigest(),
            "source_id": source.receipt.source_id,
            "source_revision_id": source.receipt.source_revision_id,
            "source_content_sha256": stored.source.sha256,
            "binding_id": source.binding.binding_id,
            "actor_id": source.binding.actor.actor_id,
        }
    )


def _skill_head(snapshot: JsonObject, skill_id: str) -> JsonObject | None:
    heads = snapshot.get("skill_heads")
    if not isinstance(heads, dict):
        return None
    head = heads.get(skill_id)
    return head if isinstance(head, dict) else None


def _run_minimal_skill_reuse(home: Path, output: Path) -> JsonObject:
    executable = resolve_codex_executable()
    if executable is None:
        raise CanaryHarnessError("official_codex_cli_unavailable")
    codex = CodexCli(
        executable=executable,
        model=os.environ.get("TRACE_MARKETING_MODEL") or "gpt-6-astra",
    )
    model_id = _model_id(codex)
    recorder = EvidenceRecorder(
        output=output,
        invocation=_invocation(),
        model_id=model_id,
        started_at=datetime.now(UTC).isoformat(),
    )
    rubric_path = output.with_suffix(".rubric.json")
    rubric: JsonObject = {
        "schema": "trace.learning-model-minimal-rubric.v1",
        "scenario": "minimal-skill-reuse",
        "registered_before_calls": True,
        "expected_token_sha256": hashlib.sha256(_MINIMAL_TOKEN.encode()).hexdigest(),
        "checks": [
            "U1 writes the requested semantic skill draft through skill_apply.",
            "The applied skill head remains identical after service restart.",
            "U2 receives the exact persisted skill revision and canonical U1 source dependency.",
            "U2 answers with the requested token only.",
            "No curation, research, external delivery, or more than five foreground calls occur.",
        ],
    }
    _ = rubric_path.write_text(json.dumps(rubric, ensure_ascii=False, indent=2) + "\n")
    recorder.checkpoint("minimal-rubric-preregistered")
    settings, paths, installation = _initialize(home)
    sender = CapturingSender(recorder)
    search = SyntheticSearchExecutor(recorder)
    before_operations: set[str]
    u1: Trial
    u1_source: JsonObject
    u1_operation_ids: tuple[str, ...]
    u1_targets: tuple[JsonObject, ...]
    u1_exchanges: tuple[JsonObject, ...]
    head_before_restart: JsonObject | None

    surface = _build_surface(
        settings=settings,
        paths=paths,
        codex=codex,
        model_id=model_id,
        installation=installation,
        recorder=recorder,
        sender=sender,
        search=search,
        max_foreground_calls=_MINIMAL_MAX_MODEL_CALLS,
        allowed_capability_ids=("skill_apply",),
    )
    try:
        before_operations = set(_applied_operations(surface))
        with _loopback(surface.api) as base_url:
            u1 = _submit(
                surface=surface,
                base_url=base_url,
                recorder=recorder,
                name="minimal-u1-explicit-skill-write",
                event_id="E-minimal-u1",
                user="U1",
                channel="C1",
                text=_MINIMAL_U1_SOURCE,
                timestamp="100.000001",
                run_learning=False,
            )
        candidate_operations = tuple(
            operation_id
            for operation_id in _applied_operations(surface)
            if operation_id not in before_operations
        )
        u1_operation_ids = _source_bound_operations(surface, candidate_operations, u1.run_id)
        u1_targets = _operation_targets(surface, u1_operation_ids)
        u1_exchanges = _tool_exchanges(u1, "skill_apply")
        u1_source = _canonical_source_lineage(surface, u1.run_id)
        head_before_restart = _skill_head(
            _state_snapshot(surface, settings, "minimal-before-restart"),
            _MINIMAL_SKILL_ID,
        )
        recorder.snapshots.append(
            {
                "label": "minimal-u1-write",
                "candidate_operation_ids": list(candidate_operations),
                "source_bound_operation_ids": list(u1_operation_ids),
                "operation_targets": list(u1_targets),
                "canonical_source": u1_source,
                "skill_head": head_before_restart,
                "skill_apply_exchanges": list(u1_exchanges),
            }
        )
        recorder.checkpoint("minimal-u1-captured")
    finally:
        surface.close()

    surface = _build_surface(
        settings=settings,
        paths=paths,
        codex=codex,
        model_id=model_id,
        installation=installation,
        recorder=recorder,
        sender=sender,
        search=search,
        max_foreground_calls=_MINIMAL_MAX_MODEL_CALLS,
        allowed_capability_ids=("skill_get",),
    )
    try:
        restarted_snapshot = _state_snapshot(surface, settings, "minimal-after-restart")
        head_after_restart = _skill_head(restarted_snapshot, _MINIMAL_SKILL_ID)
        with _loopback(surface.api) as base_url:
            u2 = _submit(
                surface=surface,
                base_url=base_url,
                recorder=recorder,
                name="minimal-u2-new-thread-reuse",
                event_id="E-minimal-u2",
                user="U2",
                channel="C1",
                text=_MINIMAL_U2_QUERY,
                timestamp="200.000001",
                run_learning=False,
            )
        selected_targets = _selected_targets(u2)
        selected_skill = next(
            (
                item
                for item in selected_targets
                if item.get("target_kind") == "skill" and item.get("target_id") == _MINIMAL_SKILL_ID
            ),
            None,
        )
        skill_get_exchanges = _tool_exchanges(u2, "skill_get")
    finally:
        surface.close()

    operation_target = next(
        (
            item
            for item in u1_targets
            if item.get("target_kind") == "skill" and item.get("target_id") == _MINIMAL_SKILL_ID
        ),
        None,
    )
    expected_source_revision = {
        "source_id": u1_source.get("source_id"),
        "revision_id": u1_source.get("source_revision_id"),
        "content_sha256": u1_source.get("source_content_sha256"),
    }
    selected_source_revisions = (
        selected_skill.get("source_revisions", []) if isinstance(selected_skill, dict) else []
    )
    apply_output = u1_exchanges[0].get("output_evidence") if len(u1_exchanges) == 1 else None
    apply_receipt = u1_exchanges[0].get("receipt") if len(u1_exchanges) == 1 else None
    apply_data = (
        apply_output.get("output")
        if isinstance(apply_output, dict) and isinstance(apply_output.get("output"), dict)
        else None
    )
    apply_result = apply_data.get("data") if isinstance(apply_data, dict) else None
    checks: JsonObject = {
        "u1_exactly_one_source_bound_skill_operation": (
            len(u1_operation_ids) == 1 and operation_target is not None
        ),
        "u1_skill_apply_exchange_bound": (
            len(u1_exchanges) == 1 and u1_exchanges[0].get("bindings_valid") is True
        ),
        "u1_skill_apply_applied_exact_target": (
            isinstance(apply_receipt, dict)
            and apply_receipt.get("disposition") == "succeeded"
            and isinstance(apply_data, dict)
            and apply_data.get("status") in {"applied", "replayed"}
            and isinstance(apply_result, dict)
            and apply_result.get("target_ids") == [_MINIMAL_SKILL_ID]
        ),
        "restart_preserved_exact_head": (
            head_before_restart is not None and head_before_restart == head_after_restart
        ),
        "u2_selected_exact_written_revision": (
            isinstance(operation_target, dict)
            and isinstance(selected_skill, dict)
            and all(
                selected_skill.get(key) == operation_target.get(key)
                for key in ("target_kind", "target_id", "revision_id", "content_sha256")
            )
        ),
        "u2_selected_u1_canonical_source_revision": (
            isinstance(selected_source_revisions, list)
            and expected_source_revision in selected_source_revisions
        ),
        "u2_skill_get_exchange_bound": (
            len(skill_get_exchanges) == 1 and skill_get_exchanges[0].get("bindings_valid") is True
        ),
        "u2_exact_response": u2.response.strip() == _MINIMAL_TOKEN,
        "foreground_call_limit": len(recorder.foreground_calls) <= _MINIMAL_MAX_MODEL_CALLS,
        "no_background_or_research_calls": (
            not recorder.curation_calls and not recorder.search_calls
        ),
        "fake_slack_only": bool(recorder.slack_sends)
        and all(item.get("simulated_outcome") == "delivered" for item in recorder.slack_sends),
    }
    passed = all(value is True for value in checks.values())
    return _JSON_OBJECT.validate_python(
        {
            "schema": "trace.learning-model-minimal-result.v1",
            "scenario": "minimal-skill-reuse",
            "status": "passed" if passed else "failed",
            "invocation": recorder.invocation,
            "started_at": recorder.started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "installed_package": str(Path(sys.modules["ads_booster"].__file__ or "").resolve()),
            "official_codex_executable": str(executable),
            "provider_model": model_id,
            "foreground_model_call_count": len(recorder.foreground_calls),
            "foreground_model_call_hard_limit": _MINIMAL_MAX_MODEL_CALLS,
            "curation_model_call_count": len(recorder.curation_calls),
            "synthetic_search_call_count": len(recorder.search_calls),
            "rubric_path": str(rubric_path),
            "rubric_sha256": contract_sha256(rubric),
            "checks": checks,
            "u1": {
                "run_id": u1.run_id,
                "source_text_sha256": hashlib.sha256(_MINIMAL_U1_SOURCE.encode()).hexdigest(),
                "response": u1.response,
                "candidate_operation_count": len(candidate_operations),
                "source_bound_operation_ids": list(u1_operation_ids),
                "operation_targets": list(u1_targets),
                "skill_apply_exchanges": list(u1_exchanges),
                "canonical_source": u1_source,
                "head_before_restart": head_before_restart,
            },
            "restart": {"head_after_restart": head_after_restart},
            "u2": {
                "run_id": u2.run_id,
                "query_sha256": hashlib.sha256(_MINIMAL_U2_QUERY.encode()).hexdigest(),
                "response": u2.response,
                "selected_skill": selected_skill,
                "selected_source_revisions": selected_source_revisions,
                "expected_u1_source_revision": expected_source_revision,
                "skill_get_exchanges": list(skill_get_exchanges),
            },
            "foreground_calls": recorder.foreground_calls,
            "curation_calls": recorder.curation_calls,
            "synthetic_search_calls": recorder.search_calls,
            "slack_sends": recorder.slack_sends,
            "trials": recorder.trials,
            "snapshots": recorder.snapshots,
            "limitations": [
                "Synthetic Slack only; no Slack network, curation, research, or external effect.",
                (
                    "This fixed semantic-rule scenario is a canary, not a general "
                    "learning-quality claim."
                ),
            ],
        }
    )


def _run(home: Path, output: Path) -> JsonObject:
    executable = resolve_codex_executable()
    if executable is None:
        raise CanaryHarnessError("official_codex_cli_unavailable")
    codex = CodexCli(
        executable=executable,
        model=os.environ.get("TRACE_MARKETING_MODEL") or "gpt-6-astra",
    )
    model_id = _model_id(codex)
    recorder = EvidenceRecorder(
        output=output,
        invocation=_invocation(),
        model_id=model_id,
        started_at=datetime.now(UTC).isoformat(),
    )
    rubric_path = output.with_suffix(".rubric.json")
    source_fixture = _source_fixture()
    rubric_payload: JsonObject = {
        **_RUBRIC,
        "registered_at": datetime.now(UTC).isoformat(),
        "rubric_sha256": contract_sha256(_RUBRIC),
        "source_fixture_sha256": contract_sha256(source_fixture),
        "source_fixture": source_fixture,
        "note": (
            "This scoring structure is written before model calls and never passed to either "
            "model. Among model inputs, the terminal token appears only in canonical synthetic "
            "tool output; the class/task tokens appear only in natural authenticated sources."
        ),
    }
    _ = rubric_path.write_text(json.dumps(rubric_payload, ensure_ascii=False, indent=2) + "\n")
    recorder.checkpoint("rubric-preregistered")
    settings, paths, installation = _initialize(home)
    sender = CapturingSender(recorder)
    search = SyntheticSearchExecutor(recorder)
    original_batch = CodexKnowledgeProvider.decide_batch
    receipt_trials: list[Trial] = []

    with patch.object(
        CodexKnowledgeProvider,
        "decide_batch",
        _recording_batch(recorder, original_batch),
    ):
        surface = _build_surface(
            settings=settings,
            paths=paths,
            codex=codex,
            model_id=model_id,
            installation=installation,
            recorder=recorder,
            sender=sender,
            search=search,
        )
        try:
            initial = _state_snapshot(surface, settings, "initial")
            recorder.snapshots.append(initial)
            with _loopback(surface.api) as base_url:
                baseline = _submit(
                    surface=surface,
                    base_url=base_url,
                    recorder=recorder,
                    name="baseline-independent-thread",
                    event_id="E-baseline",
                    user="U1",
                    channel="C1",
                    text=_BASELINE_QUERY,
                    timestamp="100.000001",
                )
                before_terminal_operations: set[str] | None = None
                before_terminal: JsonObject | None = None
                pre_terminal_receipt_count: int | None = None
                for index, prompt in enumerate(_RECEIPT_PROMPTS, start=1):
                    if index == len(_RECEIPT_PROMPTS):
                        before_terminal_operations = set(_applied_operations(surface))
                        before_terminal = _state_snapshot(
                            surface, settings, "before-terminal-receipt-ten"
                        )
                        pre_terminal_receipt_count = len(
                            _synthetic_receipt_evidence(
                                surface, tuple(trial.run_id for trial in receipt_trials)
                            )
                        )
                        before_terminal["synthetic_receipt_count"] = pre_terminal_receipt_count
                        recorder.snapshots.append(before_terminal)
                    receipt_trials.append(
                        _submit(
                            surface=surface,
                            base_url=base_url,
                            recorder=recorder,
                            name=f"terminal-receipt-{index}",
                            event_id=f"E-receipt-{index}",
                            user="U1",
                            channel="C1",
                            text=prompt,
                            timestamp=f"{110 + index}.000001",
                        )
                    )
                if (
                    before_terminal_operations is None
                    or before_terminal is None
                    or pre_terminal_receipt_count is None
                ):
                    raise CanaryHarnessError("model_canary_terminal_snapshot_missing")
                surface.installed.runtime.run_until_idle(flush_batches=True)
                _work_slack(surface.events)
            learned = _state_snapshot(surface, settings, "after-terminal-learning")
            recorder.snapshots.append(learned)
            learning_operations = tuple(
                item
                for item in _applied_operations(surface)
                if item not in before_terminal_operations
            )
            receipt_run_ids = tuple(trial.run_id for trial in receipt_trials)
            receipt_evidence = _synthetic_receipt_evidence(surface, receipt_run_ids)
            receipt_outcomes = _receipt_outcomes(receipt_evidence)
            admitted_outcomes = _admitted_receipts(surface, receipt_run_ids)
            experience_lineage = _experience_lineage(surface, recorder, receipt_run_ids)
            experience_ids = frozenset(
                str(item["experience_id"])
                for item in experience_lineage
                if isinstance(item.get("experience_id"), str)
            )
            experience_bound_operations = _experience_bound_operations(
                recorder, learning_operations, experience_ids
            )
            terminal_operation_targets = _operation_targets(surface, experience_bound_operations)
            builtin_guard = _builtin_guard(surface, settings, baseline.run_id)
            recorder.snapshots.append({"label": "builtin-codeguard", **builtin_guard})
        finally:
            surface.close()

        surface = _build_surface(
            settings=settings,
            paths=paths,
            codex=codex,
            model_id=model_id,
            installation=installation,
            recorder=recorder,
            sender=sender,
            search=search,
        )
        try:
            restarted = _state_snapshot(surface, settings, "after-restart")
            recorder.snapshots.append(restarted)
            with _loopback(surface.api) as base_url:
                after = _submit(
                    surface=surface,
                    base_url=base_url,
                    recorder=recorder,
                    name="after-independent-new-member-thread",
                    event_id="E-after",
                    user="U2",
                    channel="C1",
                    text=_BASELINE_QUERY,
                    timestamp="300.000001",
                )
                class_baseline = _submit(
                    surface=surface,
                    base_url=base_url,
                    recorder=recorder,
                    name="clear-correction-baseline",
                    event_id="E-class-baseline",
                    user="U1",
                    channel="C1",
                    text=_CLASS_QUERY,
                    timestamp="310.000001",
                )
                before_class_operations = set(_applied_operations(surface))
                class_correction = _submit(
                    surface=surface,
                    base_url=base_url,
                    recorder=recorder,
                    name="class-level-clear-correction",
                    event_id="E-class-rule",
                    user="U1",
                    channel="C1",
                    text=_CLASS_RULE_SOURCE,
                    timestamp="320.000001",
                )
                surface.installed.runtime.run_until_idle(flush_batches=True)
                _work_slack(surface.events)
                class_after = _submit(
                    surface=surface,
                    base_url=base_url,
                    recorder=recorder,
                    name="clear-correction-after-new-member-thread",
                    event_id="E-class-after",
                    user="U2",
                    channel="C1",
                    text=_CLASS_QUERY,
                    timestamp="330.000001",
                )
                class_candidate_operations = tuple(
                    item
                    for item in _applied_operations(surface)
                    if item not in before_class_operations
                )
                class_operations = _source_bound_operations(
                    surface, class_candidate_operations, class_correction.run_id
                )
                task_heads_before = _state_snapshot(surface, settings, "before-task-only")
                task_correction = _submit(
                    surface=surface,
                    base_url=base_url,
                    recorder=recorder,
                    name="task-only-current-correction",
                    event_id="E-task-rule",
                    user="U1",
                    channel="C1",
                    text=_TASK_RULE_SOURCE,
                    timestamp="400.000001",
                )
                task_same = _submit(
                    surface=surface,
                    base_url=base_url,
                    recorder=recorder,
                    name="task-only-same-task-application",
                    event_id="E-task-same",
                    user="U1",
                    channel="C1",
                    text=_TASK_QUERY,
                    timestamp="401.000001",
                    thread_timestamp="400.000001",
                )
                task_other = _submit(
                    surface=surface,
                    base_url=base_url,
                    recorder=recorder,
                    name="task-only-new-member-absence",
                    event_id="E-task-other",
                    user="U2",
                    channel="C1",
                    text=_TASK_QUERY,
                    timestamp="410.000001",
                )
                task_heads_after = _state_snapshot(surface, settings, "after-task-only")
                recorder.snapshots.extend((task_heads_before, task_heads_after))

                private_before = _state_snapshot(surface, settings, "before-private")
                _ = _submit(
                    surface=surface,
                    base_url=base_url,
                    recorder=recorder,
                    name="private-correction-excluded",
                    event_id="E-private",
                    user="U1",
                    channel="D1",
                    text="Correction: keep this private and do not change shared learning.",
                    timestamp="500.000001",
                    private=True,
                )
                private_after = _state_snapshot(surface, settings, "after-private")
                recorder.snapshots.extend((private_before, private_after))

                question_ids_before = {item.question_id for item in _questions(surface)}
                conflict = _submit(
                    surface=surface,
                    base_url=base_url,
                    recorder=recorder,
                    name="same-scope-conflict",
                    event_id="E-conflict",
                    user="U1",
                    channel="C1",
                    text=_CONFLICT_SOURCE,
                    timestamp="600.000001",
                    project_questions=False,
                )
                new_questions = tuple(
                    item
                    for item in _questions(surface)
                    if item.question_id not in question_ids_before
                )
                dynamic_question = new_questions[0] if len(new_questions) == 1 else None
                sender.fail_questions = True
                _work_slack(surface.events)
                question_attempts_after_first = sum(
                    bool(item.get("is_learning_question")) for item in recorder.slack_sends
                )
                surface.events.recover()
                _work_slack(surface.events)
                question_attempts_after_recover = sum(
                    bool(item.get("is_learning_question")) for item in recorder.slack_sends
                )
                sender.fail_questions = False
            final_snapshot = _state_snapshot(surface, settings, "final")
            recorder.snapshots.append(final_snapshot)

            selected_sources, selected_skills, selected_skill_sources = _prepared_revisions(after)
            selected_targets = _selected_targets(after)
            selected_operation_targets = _matching_selected_targets(
                terminal_operation_targets, selected_targets, experience_lineage
            )
            model_builtin_attempted, model_builtin_rejected = _model_builtin_behavior(recorder)
            question_id = None if dynamic_question is None else dynamic_question.question_id
            question_status = None if dynamic_question is None else dynamic_question.status.value
            delivery_state = (
                None if question_id is None else _question_delivery(surface, question_id)
            )
            case_results = evaluate_canary(
                learning=LearningFacts(
                    baseline_response=baseline.response,
                    after_response=after.response,
                    expected_token=_TERMINAL_TOKEN,
                    baseline_query_sha256=hashlib.sha256(_BASELINE_QUERY.encode()).hexdigest(),
                    after_query_sha256=hashlib.sha256(_BASELINE_QUERY.encode()).hexdigest(),
                    selected_source_revisions=selected_sources,
                    selected_skill_revisions=selected_skills,
                    selected_skill_source_revisions=selected_skill_sources,
                    applied_operation_ids=experience_bound_operations,
                    pre_terminal_receipt_count=pre_terminal_receipt_count,
                    head_changed_after_terminal_seal=(
                        _head_digest(before_terminal) != _head_digest(learned)
                    ),
                    experience_source_binding_count=len(experience_bound_operations),
                    selected_operation_target_count=len(selected_operation_targets),
                ),
                clear_correction=ClearCorrectionFacts(
                    baseline_response=class_baseline.response,
                    after_response=class_after.response,
                    expected_token=_CLASS_TOKEN,
                    baseline_query_sha256=hashlib.sha256(_CLASS_QUERY.encode()).hexdigest(),
                    after_query_sha256=hashlib.sha256(_CLASS_QUERY.encode()).hexdigest(),
                    source_linked_operation_ids=class_operations,
                ),
                receipts=ReceiptFacts(
                    outcomes=receipt_outcomes,
                    admitted_outcomes=admitted_outcomes,
                    curation_result_projection_count=_projected_search_results(
                        recorder, receipt_run_ids
                    ),
                    canonical_evidence_binding_count=sum(
                        item.get("binding_valid") is True for item in experience_lineage
                    ),
                ),
                boundaries=BoundaryFacts(
                    private_counter_delta=(
                        _counter_total(private_after) - _counter_total(private_before)
                    ),
                    private_head_changed=(
                        _head_digest(private_before) != _head_digest(private_after)
                    ),
                    task_overlay_applied=_task_overlay_applied(task_correction),
                    same_task_selected=_selected_task_overlay(task_same),
                    new_member_selected=_selected_task_overlay(task_other),
                    task_same_response=task_same.response,
                    task_new_member_response=task_other.response,
                    task_expected_token=_TASK_TOKEN,
                    task_overlay_in_core=_contains_token(task_heads_after, _TASK_TOKEN, "core"),
                    task_overlay_in_skill_heads=_contains_token(
                        task_heads_after, _TASK_TOKEN, "skill_heads"
                    ),
                    builtin_model_attempted=model_builtin_attempted,
                    builtin_model_attempt_rejected=model_builtin_rejected,
                    builtin_host_rejected=bool(builtin_guard.get("rejected")),
                    builtin_heads_changed=not bool(builtin_guard.get("unchanged")),
                    conflict_question_id=question_id,
                    conflict_status=question_status,
                    conflict_delivery_state=delivery_state,
                    conflict_retry_count=(
                        question_attempts_after_recover - question_attempts_after_first
                    ),
                    conflict_approval_records=_approval_records(surface, conflict.run_id),
                ),
            )
        finally:
            surface.close()

    all_passed = all(item["status"] == "passed" for item in case_results.values())
    return _JSON_OBJECT.validate_python(
        {
            "schema": "trace.learning-model-canary-result.v1",
            "status": "passed" if all_passed else "failed",
            "invocation": recorder.invocation,
            "started_at": recorder.started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "installed_package": str(Path(sys.modules["ads_booster"].__file__ or "").resolve()),
            "knowledge_roots": [
                str(settings.root),
                str(settings.control_root),
                str(settings.policy_path),
            ],
            "official_codex_executable": str(executable),
            "provider_model": model_id,
            "foreground_model_call_count": len(recorder.foreground_calls),
            "curation_model_call_count": len(recorder.curation_calls),
            "synthetic_search_call_count": len(recorder.search_calls),
            "rubric_path": str(rubric_path),
            "rubric_sha256": contract_sha256(_RUBRIC),
            "source_fixture_sha256": contract_sha256(source_fixture),
            "source_fixture": source_fixture,
            "baseline_query_sha256": hashlib.sha256(_BASELINE_QUERY.encode()).hexdigest(),
            "baseline": {
                "run_id": baseline.run_id,
                "response": baseline.response,
                "reasoning_calls": list(baseline.reasoning_calls),
            },
            "after": {
                "run_id": after.run_id,
                "response": after.response,
                "reasoning_calls": list(after.reasoning_calls),
                "selected_source_revisions": list(selected_sources),
                "selected_skill_revisions": list(selected_skills),
                "selected_skill_source_revisions": list(selected_skill_sources),
            },
            "terminal_receipts": {
                "run_ids": list(receipt_run_ids),
                "ordered_receipts": list(receipt_evidence),
                "observed": list(receipt_outcomes),
                "admitted": list(admitted_outcomes),
                "canonical_experience_lineage": list(experience_lineage),
                "experience_bound_operation_ids": list(experience_bound_operations),
                "terminal_operation_targets": list(terminal_operation_targets),
                "after_selected_targets": list(selected_targets),
                "matched_selected_operation_targets": list(selected_operation_targets),
            },
            "clear_correction": {
                "baseline_run_id": class_baseline.run_id,
                "baseline_response": class_baseline.response,
                "after_run_id": class_after.run_id,
                "after_response": class_after.response,
                "operation_ids": list(class_operations),
            },
            "case_results": case_results,
            "foreground_calls": recorder.foreground_calls,
            "curation_calls": recorder.curation_calls,
            "synthetic_search_calls": recorder.search_calls,
            "slack_sends": recorder.slack_sends,
            "trials": recorder.trials,
            "snapshots": recorder.snapshots,
            "limitations": [
                (
                    "Synthetic Slack and search only; no Slack network, publication, image, or "
                    "paid effect."
                ),
                (
                    "The rubric grades these fixed scenarios only and is not a general "
                    "learning-quality claim."
                ),
                (
                    "Host builtin rejection and model background behavior are reported as "
                    "separate evidence."
                ),
            ],
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Opt-in fresh-wheel real-model canary for minimal skill learning and reuse.",
        epilog=(
            "Run from outside the checkout with PYTHONPATH unset: "
            "/absolute/venv/bin/python -I /absolute/checkout/tests/operations/"
            "installed_learning_model_canary.py --scenario minimal-skill-reuse "
            "--home /absolute/new-home "
            "--output /absolute/checkout/.omo/evidence/agent-feedback-learning/f4-model.json"
        ),
    )
    _ = parser.add_argument("--scenario", choices=("minimal-skill-reuse",), required=True)
    _ = parser.add_argument("--home", type=Path, required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(namespace=Arguments())
    home = arguments.home.resolve()
    output = arguments.output.resolve()
    _assert_paths(home, output)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        result = _run_minimal_skill_reuse(home, output)
    except (OSError, RuntimeError, ValueError) as error:
        if output.exists():
            failed = _JSON_OBJECT.validate_json(output.read_bytes())
        else:
            failed = {
                "schema": "trace.learning-model-canary-evidence.v1",
                "status": "failed",
            }
        failed["status"] = "failed"
        failed["error_type"] = type(error).__name__
        failed["error"] = str(error)
        _ = output.write_text(json.dumps(failed, ensure_ascii=False, indent=2) + "\n")
        raise
    _ = output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if result.get("status") != "passed":
        raise CanarySemanticError("model_canary_semantic_cases_failed")


__all__ = [
    "BoundaryFacts",
    "ClearCorrectionFacts",
    "EvidenceRecorder",
    "LearningFacts",
    "ReceiptFacts",
    "_run",
    "evaluate_canary",
]


if __name__ == "__main__":
    main()
