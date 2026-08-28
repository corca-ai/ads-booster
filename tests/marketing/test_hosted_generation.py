"""The Mac worker's half of hosted caption generation.

The hosted control plane publishes a job and this executor runs the same generator the local
surface runs. What these tests hold is the seam: what comes out of the payload, what goes
into the engine, what comes back over the callback, and that the image path is untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest

from ads_booster.candidate_generation.draft_engine import (
    CandidateDraftBatch,
    GeneratedCandidate,
)
from ads_booster.candidate_generation.errors import (
    CandidateAuthRequiredError,
    CandidateContextMissingError,
    CandidateProviderError,
)
from ads_booster.candidate_generation.instruction import CaptionForm
from ads_booster.candidate_generation.models import CandidateDraft
from ads_booster.marketing.hosted_generation import (
    PIPELINE,
    HostedGenerationRoutingExecutor,
    HostedWorkspaceGenerationExecutor,
)
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskResult, TaskStatus
from ads_booster.workspace import (
    CandidateBackgroundSubject,
    CandidateContextDocument,
    CandidateGenerationProvenance,
    CandidateImageInputs,
    CandidatePersonaDomain,
    CandidatePostingSlot,
)

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject, JsonValue
    from ads_booster.workspace import CandidateAccountBrief, CandidateHistoryEntry

_IDENTITY: Final[JsonObject] = {
    "display_name": "이서진",
    "age": 27,
    "region": "서울 마포구",
    "occupation": "병동 간호사",
    "concept": "3교대를 잠금화면 일정으로 버티는 간호사",
    "domain": "office_worker",
    "interests": ["쿠로미", "필라테스"],
    "life_rhythm": "데이 출근일 5시 40분 기상",
    "taste": {
        "background_subject": "character_other",
        "background_mood": "파스텔 톤의 캐릭터 배경",
        "font": "sf_pro_rounded",
    },
}


def _draft(topic: str = "야간 근무 전날 밤") -> CandidateDraft:
    return CandidateDraft(
        topic=topic,
        country="KR",
        posting_slot=CandidatePostingSlot.EVENING,
        persona_domain=CandidatePersonaDomain.OFFICE_WORKER,
        caption=f"{topic} — 오늘은 일찍 눕는다",
        hypothesis="교대 근무의 하루 리듬이 공감을 만든다",
        refs_used=("kr-001",),
        principles_applied=(3,),
        appium_prompt="입력_일정: 05:40 기상",
        image_inputs=CandidateImageInputs(
            trace_items=("05:40 기상", "20:00 인계"),
            device_time="22:40",
            background_subject=CandidateBackgroundSubject.CHARACTER_OTHER,
            background_mood="파스텔 톤의 캐릭터 배경",
            background_search_query="쿠로미 배경화면",
            language="ko",
        ),
    )


def _provenance() -> CandidateGenerationProvenance:
    return CandidateGenerationProvenance(
        documents=(CandidateContextDocument(relative_path="core/FACTS.md", size_bytes=1200),),
        model="gpt-5.6-codex",
        instruction_chars=18_240,
        generated_at=1_756_000_000.0,
        assigned_domains=(CandidatePersonaDomain.OFFICE_WORKER,),
        reference_ids=("kr-001", "kr-014"),
    )


@dataclass(frozen=True, slots=True)
class DraftCall:
    """One call the executor made, kept so a test can read back what it asked for."""

    corpus_country: str
    draft_country: str
    domains: tuple[CandidatePersonaDomain, ...]
    brief: CandidateAccountBrief | None
    history: tuple[CandidateHistoryEntry, ...]


@dataclass(slots=True)
class FakeEngine:
    """Records what the executor asked for and answers with fixed drafts."""

    batch: CandidateDraftBatch | None = None
    error: Exception | None = None
    calls: list[DraftCall] = field(default_factory=list)
    # Shared with the execution barrier in one test, so the order of the two is observable.
    trace: list[str] = field(default_factory=list)

    def draft(
        self,
        *,
        corpus_country: str,
        draft_country: str,
        domains: tuple[CandidatePersonaDomain, ...],
        brief: CandidateAccountBrief | None = None,
        history: tuple[CandidateHistoryEntry, ...] = (),
    ) -> CandidateDraftBatch:
        self.calls.append(
            DraftCall(
                corpus_country=corpus_country,
                draft_country=draft_country,
                domains=domains,
                brief=brief,
                history=history,
            )
        )
        self.trace.append("draft")
        if self.error is not None:
            raise self.error
        assert self.batch is not None
        return self.batch


def _batch(count: int = 1, failures: int = 0) -> CandidateDraftBatch:
    return CandidateDraftBatch(
        drafts=tuple(
            GeneratedCandidate(
                draft=_draft(f"하루 {index}"),
                provenance=_provenance(),
                caption_form=CaptionForm.DAILY,
            )
            for index in range(count)
        ),
        failures=failures,
    )


def _payload(**overrides: JsonValue) -> JsonObject:
    payload: JsonObject = {
        "pipeline": PIPELINE,
        "persona_id": "persona-1",
        "persona": dict(_IDENTITY),
        "country": "KR",
        "language": "ko",
        "count": 2,
        "context_profile_id": "profile-1",
        "requested_by": "hosted_workspace",
    }
    payload.update(overrides)
    return payload


def _task(payload: JsonObject | None = None) -> MarketingTask:
    return MarketingTask(
        task_id="task-1",
        run_id="run-1",
        account_id="trace_demo_kr",
        kind=TaskKind.GENERATE_CANDIDATES,
        idempotency_key="hosted-generation:trace_demo_kr:task-1",
        payload=_payload() if payload is None else payload,
        created_at=datetime.now(UTC),
    )


def test_the_persona_identity_becomes_the_generation_brief() -> None:
    """The hosted persona is the local account identity, so it is read by that model."""
    # Given a published job carrying one persona's whole identity
    engine = FakeEngine(batch=_batch(2))
    executor = HostedWorkspaceGenerationExecutor(engine=engine)

    # When the worker runs it
    result = executor.execute(_task())

    # Then the brief the instruction is built from is that person, and the batch is that one
    # person writing twice rather than two coverage domains.
    call = engine.calls[0]
    assert call.brief is not None
    assert call.brief.display_name == "이서진"
    assert call.brief.domain is CandidatePersonaDomain.OFFICE_WORKER
    assert call.brief.background_subject is CandidateBackgroundSubject.CHARACTER_OTHER
    assert call.domains == (CandidatePersonaDomain.OFFICE_WORKER,) * 2
    assert result.status is TaskStatus.SUCCEEDED


def test_the_corpus_follows_the_persona_and_the_drafts_follow_the_template() -> None:
    """Two countries, on purpose: the corpus is the persona's, the output is the template's.

    The instruction template names KR in so many words, so a batch written for another
    country still has to come back as KR — exactly as the local generator holds it.
    """
    # Given a job for a JP persona
    engine = FakeEngine(batch=_batch())
    # When the worker runs it
    _ = HostedWorkspaceGenerationExecutor(engine=engine).execute(_task(_payload(country="JP")))

    # Then the reference corpus is Japan's and the draft contract is still KR
    assert engine.calls[0].corpus_country == "JP"
    assert engine.calls[0].draft_country == "KR"


def test_a_country_wide_job_has_no_brief_and_still_covers_distinct_domains() -> None:
    # Given a job with no persona, which is what the daily scheduled batch publishes
    engine = FakeEngine(batch=_batch(3))
    # When the worker runs it
    result = HostedWorkspaceGenerationExecutor(engine=engine).execute(
        _task(_payload(persona=None, persona_id=None, count=3))
    )

    # Then there is nobody to write as, and the batch still spreads over three domains
    call = engine.calls[0]
    assert call.brief is None
    assert len(call.domains) == 3
    assert len(set(call.domains)) == 3
    assert result.output["persona_id"] is None


def test_the_result_carries_the_drafts_and_what_produced_them() -> None:
    # Given a batch that produced two of the three it was asked for
    executor = HostedWorkspaceGenerationExecutor(engine=FakeEngine(batch=_batch(2, failures=1)))

    # When the worker answers
    result = executor.execute(_task())

    # Then the drafts travel in the shape the hosted candidate table stores
    assert result.output["requested"] == 2
    assert result.output["failures"] == 1
    candidates = result.output["candidates"]
    assert isinstance(candidates, list)
    first = candidates[0]
    assert isinstance(first, dict)
    assert first["country"] == "KR"
    assert first["posting_slot"] == "evening"
    assert first["persona_domain"] == "office_worker"
    image_inputs = first["image_inputs"]
    assert isinstance(image_inputs, dict)
    # The wallpaper query the model wrote is what the image stage searches with.
    assert image_inputs["background_search_query"] == "쿠로미 배경화면"
    # And the generator's own record travels whole, because the review panel renders it.
    provenance = first["provenance"]
    assert isinstance(provenance, dict)
    assert provenance["model"] == "gpt-5.6-codex"
    assert provenance["instruction_chars"] == 18_240
    assert provenance["reference_ids"] == ["kr-001", "kr-014"]
    assert provenance["caption_form"] == "daily"
    assert provenance["documents"] == [{"relative_path": "core/FACTS.md", "size_bytes": 1200}]


def test_the_execution_barrier_is_crossed_before_any_provider_call() -> None:
    """The control plane refuses a callback for a task that never crossed it."""
    # Given a worker that records the order of what it did
    engine = FakeEngine(batch=_batch())

    def barrier(task_id: str) -> None:
        engine.trace.append(f"barrier:{task_id}")

    # When it runs one job
    _ = HostedWorkspaceGenerationExecutor(engine=engine, before_execution=barrier).execute(_task())

    # Then the barrier was crossed first, so the batch cannot be handed to a second Mac
    assert engine.trace == ["barrier:task-1", "draft"]


@pytest.mark.parametrize(
    ("error", "failure_code"),
    [
        (CandidateAuthRequiredError(), "hosted_generation_ai_login_required"),
        (
            CandidateContextMissingError(__file__),  # pyright: ignore[reportArgumentType]
            "hosted_generation_context_missing",
        ),
        (CandidateProviderError(provider_code="provider_network"), "hosted_generation_failed"),
    ],
)
def test_each_generation_failure_reaches_the_browser_as_its_own_code(
    error: Exception,
    failure_code: str,
) -> None:
    """A missing Codex login is fixed on the Mac, not in the browser, so it says so."""
    # Given an engine that fails one way
    executor = HostedWorkspaceGenerationExecutor(engine=FakeEngine(error=error))

    # When the worker runs the job
    with pytest.raises(MarketingExecutionError) as raised:
        _ = executor.execute(_task())

    # Then the failure code names what has to be done about it
    assert raised.value.failure_code == failure_code


@pytest.mark.parametrize(
    ("payload_key", "value", "failure_code"),
    [
        ("country", "", "hosted_generation_country_invalid"),
        ("country", "korea", "hosted_generation_country_invalid"),
        ("count", 0, "hosted_generation_count_invalid"),
        ("count", 99, "hosted_generation_count_invalid"),
        ("persona", {"display_name": "이서진"}, "hosted_generation_persona_invalid"),
    ],
)
def test_an_unusable_payload_fails_before_a_provider_call_is_spent(
    payload_key: str,
    value: JsonValue,
    failure_code: str,
) -> None:
    # Given a job the control plane should never have published
    engine = FakeEngine(batch=_batch())

    # When the worker reads it
    with pytest.raises(MarketingExecutionError) as raised:
        _ = HostedWorkspaceGenerationExecutor(engine=engine).execute(
            _task(_payload(**{payload_key: value}))
        )

    # Then it is refused before anything is asked of the model
    assert raised.value.failure_code == failure_code
    assert engine.calls == []


def test_a_job_from_another_pipeline_is_refused_rather_than_guessed_at() -> None:
    # Given a generation-kind task from some other pipeline
    # When the worker reads it
    with pytest.raises(MarketingExecutionError) as raised:
        _ = HostedWorkspaceGenerationExecutor(engine=FakeEngine()).execute(
            _task(_payload(pipeline="something_else"))
        )

    assert raised.value.failure_code == "unsupported_hosted_generation_pipeline"


@dataclass(frozen=True, slots=True)
class _RecordingFallback:
    seen: list[str] = field(default_factory=list)

    def execute(self, task: MarketingTask) -> TaskResult:
        self.seen.append(task.kind.value)
        return TaskResult(status=TaskStatus.SUCCEEDED, output={"fallback": True})


def test_routing_sends_generation_to_the_engine_and_leaves_capture_alone() -> None:
    """The image path is one worker's other job, and it must not change shape."""
    # Given a worker that can do both
    engine = FakeEngine(batch=_batch())
    fallback = _RecordingFallback()
    executor = HostedGenerationRoutingExecutor(
        generation=HostedWorkspaceGenerationExecutor(engine=engine),
        fallback=fallback,
    )
    capture = MarketingTask(
        task_id="task-2",
        run_id="run-2",
        account_id="trace_demo_kr",
        kind=TaskKind.CAPTURE,
        idempotency_key="hosted:trace_demo_kr:candidate-1:1",
        payload={"pipeline": "hosted_workspace_capture_v1", "candidate_id": "candidate-1"},
        created_at=datetime.now(UTC),
    )

    # When one job of each kind arrives
    generated = executor.execute(_task())
    captured = executor.execute(capture)

    # Then each went where it belongs, and the capture executor saw its task unchanged
    assert generated.output["pipeline"] == PIPELINE
    assert captured.output == {"fallback": True}
    assert fallback.seen == ["capture"]
    assert len(engine.calls) == 1
