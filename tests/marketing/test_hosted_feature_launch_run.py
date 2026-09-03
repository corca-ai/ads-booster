from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import pytest

from ads_booster.contracts.marketing_agent import contract_sha256
from ads_booster.contracts.marketing_capability import (
    FeatureLaunchIntentSnapshot,
    FeatureLaunchNextIntentDecision,
    ResearchActionIdentifier,
    ResearchBoundInvocationProof,
    ResearchCapabilityScope,
    ResearchCapabilitySnapshot,
    ResearchHandResultProof,
    ResearchObservationProof,
    ResearchProofChainEntry,
    ResearchToolCallProof,
    ResearchToolReceiptProof,
)
from ads_booster.contracts.marketing_context import (
    CustomerSignalKind,
    CustomerSignalPlanningProjection,
    MarketingContextPlanningProjection,
)
from ads_booster.marketing.dynamic_evidence_research import (
    DynamicEvidenceFinding,
    DynamicEvidenceResearchRequest,
    DynamicEvidenceResearchResult,
    DynamicResearchToolRequest,
    ResearchContinuation,
    build_dynamic_research_registry,
    build_local_research_capability_snapshot,
)
from ads_booster.marketing.evidence_research_operator import (
    EvidenceResearchGoal,
    PlannerInvocationReceipt,
    ResearchDecision,
    ResearchScope,
)
from ads_booster.marketing.feature_launch_run import FeatureLaunchRunRequest
from ads_booster.marketing.hosted_feature_launch_run import (
    HostedFeatureLaunchRunExecutor,
    build_feature_launch_intent_snapshot,
    next_intent_input_schema_sha256,
    next_intent_output_schema_sha256,
    next_intent_planner_protocol_sha256,
    select_feature_launch_next_intent,
)
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskStatus
from ads_booster.marketing.runtime import bind_tool_invocation
from ads_booster.providers.codex_cli import CodexCliError

if TYPE_CHECKING:
    from ads_booster.providers.codex_cli import CodexCli
    from ads_booster.transport.json_types import JsonObject


def launch_request(*, include_customer: bool = False) -> FeatureLaunchRunRequest:
    example = Path(__file__).parents[2] / "docs" / "examples" / "feature-launch-shadow.json"
    request = FeatureLaunchRunRequest.model_validate_json(example.read_text(encoding="utf-8"))
    if not include_customer:
        return request
    research = request.research.model_copy(
        update={
            "required_scopes": (
                ResearchScope.PRODUCT_TRUTH,
                ResearchScope.CUSTOMER_INTELLIGENCE,
                ResearchScope.MARKET_EVIDENCE,
            ),
            "max_tool_calls": 3,
            "max_cost_units": 5,
        }
    )
    return request.model_copy(update={"research": research})


def resume_launch_request() -> tuple[FeatureLaunchRunRequest, str]:
    initial = launch_request(include_customer=True)
    now = datetime(2026, 9, 3, tzinfo=UTC)
    context = MarketingContextPlanningProjection(
        schema_version="trace.marketing-context-projection.v1",
        snapshot_id="customer-context-resume",
        snapshot_sha256="4" * 64,
        account_id=initial.research.account_id,
        brand_guardrails=("Do not overclaim automation.",),
        audience_context=("Korean iPhone users",),
        channel_policy_ids=("threads-default-off",),
        customer_signals=(
            CustomerSignalPlanningProjection(
                schema_version="trace.customer-signal-projection.v1",
                signal_id="customer-signal-resume",
                signal_sha256="5" * 64,
                audience_segment_id="iphone-users",
                kind=CustomerSignalKind.DESIRED_OUTCOME,
                summary="Approved users want clearer scheduled changes.",
                caveats=("Small qualitative sample.",),
                confidence_basis_points=6500,
                observed_at=now - timedelta(days=1),
                fresh_until=now + timedelta(days=30),
            ),
        ),
        expires_at=now + timedelta(days=7),
    )
    research = initial.research.model_copy(
        update={
            "session_id": "ai-lock-screen-research-resume-two",
            "marketing_context": context,
        }
    )
    resumed = initial.model_copy(
        update={
            "research": research,
            "marketing_context_snapshot_id": context.snapshot_id,
        }
    )
    return FeatureLaunchRunRequest.model_validate(resumed.model_dump(mode="json")), contract_sha256(
        initial
    )


def task(  # noqa: PLR0913 - fixture exposes the complete v5 lineage surface.
    request: FeatureLaunchRunRequest,
    *,
    account_id: str | None = None,
    credential_ref: str | None = None,
    phase: Literal["initial", "resume"] = "initial",
    step_sequence: int = 1,
    parent_step_sha256: str | None = None,
    root_request_sha256: str | None = None,
    resumable_scopes: tuple[ResearchCapabilityScope, ...] | None = None,
) -> MarketingTask:
    capability_snapshot = build_local_research_capability_snapshot(request.research.required_scopes)
    request_sha256 = contract_sha256(request)
    default_resumable = (
        ("customer_intelligence",)
        if ResearchScope.CUSTOMER_INTELLIGENCE in request.research.required_scopes
        else ()
    )
    return MarketingTask(
        task_id="feature-launch-task-one",
        run_id="agent-task-feature-launch-one",
        account_id=account_id or request.research.account_id,
        kind=TaskKind.MARKETING_JUDGMENT,
        idempotency_key="feature-launch-run:trace-kr:one",
        payload=cast(
            "JsonObject",
            {
                "pipeline": "hosted_marketing_agent_run_v5",
                "judgment": "feature_launch_run",
                "run_id": request.agent_run_id,
                "phase": phase,
                "step_sequence": step_sequence,
                "parent_step_sha256": parent_step_sha256,
                "root_request_sha256": root_request_sha256 or request_sha256,
                "resumable_scopes": list(
                    default_resumable if resumable_scopes is None else resumable_scopes
                ),
                "request_sha256": request_sha256,
                "launch_request": request.model_dump(mode="json"),
                "capability_snapshot": capability_snapshot.model_dump(mode="json"),
                "capability_snapshot_sha256": contract_sha256(capability_snapshot),
                "model_id": "gpt-test",
                "requested_by": "hosted_workspace",
            },
        ),
        created_at=datetime.now(UTC),
        credential_ref=credential_ref,
    )


def test_host_and_worker_research_capability_contract_constants_match() -> None:
    request = launch_request()
    snapshot = build_local_research_capability_snapshot(request.research.required_scopes)
    source = (
        Path(__file__).parents[2] / "cloudflare" / "src" / "marketing-run-capabilities.js"
    ).read_text(encoding="utf-8")

    def javascript_constant(name: str) -> str:
        match = re.search(rf'{name}\s*=\s*\n?\s*"([a-f0-9]{{64}})"', source)
        assert match is not None
        return match.group(1)

    assert javascript_constant("RESEARCH_SKILL_SHA256") == snapshot.skill_sha256
    assert (
        javascript_constant("RESEARCH_PLANNER_PROTOCOL_SHA256") == snapshot.planner_protocol_sha256
    )
    assert javascript_constant("RESEARCH_TOOL_REQUEST_SCHEMA_SHA256") == (
        snapshot.capabilities[0].request_schema_sha256
    )
    assert contract_sha256(snapshot) == (
        "f1d9eb6cb816e0cf9b6e4d5f94bd13f6321846551833ccf1c01e2cc697e6c208"
    )


def test_feature_launch_intent_contract_constants_are_deterministic() -> None:
    assert next_intent_input_schema_sha256() == (
        "217b305284a2eeffc4c15aa244e79dd6da6fce1a7138d656a9f27c7d5477f6fc"
    )
    assert next_intent_output_schema_sha256() == (
        "38cf82491b68ac5d14a64a6c5e83733f5a9df58b0e4b50fbac2efab161a1a8a2"
    )
    assert next_intent_planner_protocol_sha256() == (
        "64890efb66606cc77e5facacaf4c7f62ee1cad18f60247548a1eda98f5566826"
    )


def proof_chain(
    research: DynamicEvidenceResearchRequest,
    snapshot: ResearchCapabilitySnapshot,
) -> tuple[ResearchProofChainEntry, ...]:
    registry = build_dynamic_research_registry(research.required_scopes)
    entries: list[ResearchProofChainEntry] = []
    for sequence, action in enumerate(registry.actions, start=1):
        decision = ResearchDecision(
            schema_version="trace.evidence-research-decision.v2",
            decision_id=f"decision-{sequence}",
            goal_id=research.session_id,
            iteration=sequence,
            skill_id=snapshot.skill_id,
            skill_sha256=snapshot.skill_sha256,
            action_id=cast("ResearchActionIdentifier", action.action_id),
            scope=action.scope,
            claim_ids=(research.feature_packet.claims[0].claim_id,),
            research_question=f"What evidence changes {action.scope.value}?",
            counter_evidence_question=f"What refutes {action.scope.value}?",
            planner_receipt=PlannerInvocationReceipt(
                schema_version="trace.planner-invocation-receipt.v1",
                provider_id="official-codex-cli",
                model_id="gpt-test",
                prompt_sha256="1" * 64,
                context_sha256="2" * 64,
                output_schema_sha256="3" * 64,
                planner_protocol_sha256=snapshot.planner_protocol_sha256,
            ),
        )
        request_model = DynamicResearchToolRequest(
            schema_version="trace.evidence-research-tool-request.v1",
            goal=EvidenceResearchGoal(
                schema_version="trace.evidence-research-goal.v2",
                goal_id=research.session_id,
                feature_packet_id=research.feature_packet.packet_id,
                feature_packet_sha256=contract_sha256(research.feature_packet),
                input_snapshot_sha256=contract_sha256(research),
                planner_provider_id="official-codex-cli",
                planner_model_id="gpt-test",
                planner_protocol_sha256=snapshot.planner_protocol_sha256,
                pinned_skill_registry_sha256=contract_sha256(snapshot),
                required_scopes=research.required_scopes,
                max_iterations=len(research.required_scopes),
            ),
            feature_packet_sha256=contract_sha256(research.feature_packet),
            decision=decision,
        )
        request_json = cast("JsonObject", request_model.model_dump(mode="json"))
        invocation = bind_tool_invocation(
            action.capability,
            call_id=f"research-{sequence}",
            idempotency_key=f"research:{sequence}",
            request=request_json,
        )
        call = ResearchToolCallProof(
            schema_version="trace.tool-call.v1",
            call_id=invocation.call.call_id,
            idempotency_key=invocation.call.idempotency_key,
            capability_id=cast("ResearchActionIdentifier", action.capability.capability_id),
            descriptor_sha256=action.capability.descriptor_sha256,
            request_schema_sha256=action.capability.request_schema_sha256,
            input_sha256=invocation.call.input_sha256,
            effect_class="observe",
        )
        hand = ResearchHandResultProof(
            schema_version="trace.dynamic-research-hand-result-proof.v1",
            goal_id=research.session_id,
            call_id=call.call_id,
            call_sha256=call.digest,
            request_sha256=call.input_sha256,
            feature_packet_sha256=contract_sha256(research.feature_packet),
            decision_sha256=contract_sha256(decision),
            disposition="succeeded",
            actual_cost_units=action.capability.worst_case_cost_units,
            iteration=sequence,
            scope=action.scope.value,
            evidence_status="insufficient",
            source_ref=f"fixture:{action.scope.value}",
            source_sha256=str(sequence) * 64,
            trust_state="unverified_model_proposal",
            summary=f"Bound {action.scope.value} fixture.",
            observed_at=datetime(2026, 9, 3, tzinfo=UTC),
        )
        receipt = ResearchToolReceiptProof(
            call_id=call.call_id,
            call_sha256=call.digest,
            approval_grant_sha256=None,
            disposition="succeeded",
            actual_cost_units=hand.actual_cost_units,
            receipt_sha256=hand.digest,
        )
        observation = ResearchObservationProof(
            schema_version="trace.evidence-research-observation.v2",
            observation_id=f"observation-{sequence}",
            scope=action.scope.value,
            receipt_sha256=receipt.receipt_sha256,
            call_sha256=call.digest,
            request_sha256=call.input_sha256,
            feature_packet_sha256=hand.feature_packet_sha256,
            decision_sha256=hand.decision_sha256,
            source_ref=hand.source_ref,
            source_sha256=hand.source_sha256,
            evidence_summary=hand.summary,
            caveats=hand.caveats,
            trust_state=hand.trust_state,
            supported_claim_ids=hand.supported_claim_ids,
            evidence_status="insufficient",
            observed_at=hand.observed_at,
        )
        entries.append(
            ResearchProofChainEntry(
                sequence=sequence,
                iteration=sequence,
                action_id=cast("ResearchActionIdentifier", action.action_id),
                scope=action.scope.value,
                call_sha256=call.digest,
                request_sha256=call.input_sha256,
                receipt_sha256=receipt.receipt_sha256,
                observation_sha256=contract_sha256(observation),
                actual_cost_units=receipt.actual_cost_units,
                invocation=ResearchBoundInvocationProof(
                    schema_version="trace.bound-tool-invocation.v1",
                    call=call,
                    request=request_json,
                ),
                receipt=receipt,
                observation=observation,
                hand_result=hand,
            )
        )
    return tuple(entries)


def test_hosted_feature_launch_executes_only_bound_dynamic_research(  # noqa: PLR0915
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = launch_request(include_customer=True)
    observed: dict[str, object] = {}

    class FakeRunner:
        def __init__(
            self,
            codex: CodexCli,
            state_root: Path,
            model_id: str,
            timeout_seconds: float,
        ) -> None:
            observed.update(
                codex_model=codex.model,
                state_root=state_root,
                model_id=model_id,
                timeout_seconds=timeout_seconds,
            )

        def run(
            self,
            research: DynamicEvidenceResearchRequest,
            *,
            capability_snapshot: ResearchCapabilitySnapshot,
        ) -> DynamicEvidenceResearchResult:
            observed["research"] = research
            observed["capability_snapshot"] = capability_snapshot
            receipt_chain = proof_chain(research, capability_snapshot)
            result = DynamicEvidenceResearchResult(
                schema_version="trace.dynamic-evidence-research-result.v4",
                session_id=research.session_id,
                state="inconclusive",
                input_snapshot_sha256=contract_sha256(research),
                registry_snapshot_sha256=contract_sha256(capability_snapshot),
                planner_protocol_sha256=capability_snapshot.planner_protocol_sha256,
                provider_id="official-codex-cli",
                model_id="gpt-test",
                trace_sha256="3" * 64,
                tool_calls=len(receipt_chain),
                spent_cost_units=sum(item.actual_cost_units for item in receipt_chain),
                capability_snapshot=capability_snapshot,
                receipt_chain=receipt_chain,
                findings=(
                    DynamicEvidenceFinding(
                        iteration=1,
                        scope=ResearchScope.CUSTOMER_INTELLIGENCE,
                        evidence_status="insufficient",
                        summary="Customer evidence remains incomplete.",
                        source_ref="fixture:customer_intelligence",
                        source_sha256="1" * 64,
                        trust_state="packet_bound",
                    ),
                ),
            )
            observed["research_result_model"] = result
            return result

    monkeypatch.setattr(
        "ads_booster.marketing.hosted_feature_launch_run.DynamicEvidenceResearchRunner",
        FakeRunner,
    )

    def choose_more_evidence(
        _codex: object,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        observed.update(
            intent_prompt=prompt,
            intent_schema=schema,
            intent_workspace=workspace,
            intent_timeout_seconds=timeout_seconds,
        )
        return {
            "intent_id": "request_more_evidence",
            "reason": "The customer evidence remains insufficient.",
            "requested_scope": "customer_intelligence",
        }

    monkeypatch.setattr(
        "ads_booster.marketing.hosted_feature_launch_run.CodexCli.run_marketing_judgment_job",
        choose_more_evidence,
    )
    executor = HostedFeatureLaunchRunExecutor(
        codex_executable=Path("/usr/bin/true"),
        output_root=tmp_path,
    )

    prepared = executor.prepare(task(request))
    result = executor.execute(prepared)

    assert result.status is TaskStatus.SUCCEEDED
    assert observed["research"] == request.research
    assert observed["capability_snapshot"] == build_local_research_capability_snapshot(
        request.research.required_scopes
    )
    assert observed["codex_model"] == "gpt-test"
    assert result.output["task_id"] == "feature-launch-task-one"
    assert result.output["run_id"] == request.agent_run_id
    assert result.output["phase"] == "initial"
    assert result.output["step_sequence"] == 1
    assert result.output["parent_step_sha256"] is None
    assert result.output["root_request_sha256"] == contract_sha256(request)
    assert result.output["resumable_scopes"] == ["customer_intelligence"]
    assert result.output["account_id"] == request.research.account_id
    assert result.output["request_sha256"] == contract_sha256(request)
    assert result.output["research_input_sha256"] == contract_sha256(request.research)
    assert result.output["capability_snapshot_sha256"] == contract_sha256(
        build_local_research_capability_snapshot(request.research.required_scopes)
    )
    research_result = result.output["research_result"]
    assert isinstance(research_result, dict)
    assert result.output["receipt_chain"] == research_result["receipt_chain"]
    intent_snapshot = result.output["intent_snapshot"]
    assert isinstance(intent_snapshot, dict)
    intent_snapshot_model = FeatureLaunchIntentSnapshot.model_validate(intent_snapshot)
    assert [item.intent_id for item in intent_snapshot_model.intents] == [
        "stop",
        "request_more_evidence",
    ]
    assert all(item.effect_class == "none" for item in intent_snapshot_model.intents)
    assert all(item.fixed_cost_units == 0 for item in intent_snapshot_model.intents)
    assert all(item.approval_policy == "none" for item in intent_snapshot_model.intents)
    assert intent_snapshot_model.intents[1].precondition == "needs_input_terminal_projection"
    assert result.output["intent_snapshot_sha256"] == contract_sha256(intent_snapshot_model)
    next_intent = result.output["next_intent"]
    assert isinstance(next_intent, dict)
    next_intent_model = FeatureLaunchNextIntentDecision.model_validate(next_intent)
    assert next_intent_model.intent_id == "request_more_evidence"
    assert next_intent_model.requested_scope == "customer_intelligence"
    assert next_intent_model.research_result_sha256 == result.output["research_result_sha256"]
    assert next_intent_model.intent_snapshot_sha256 == result.output["intent_snapshot_sha256"]
    assert next_intent_model.planner_receipt.provider_id == "official-codex-cli"
    assert result.output["next_intent_sha256"] == contract_sha256(next_intent_model)
    assert "independently proves" in cast("str", observed["intent_prompt"])
    research_result_model = cast("DynamicEvidenceResearchResult", observed["research_result_model"])
    continuation = ResearchContinuation(
        schema_version="trace.research-continuation.v1",
        continuation_id="continuation-fixture",
        account_id=request.research.account_id,
        feature_packet_id=request.research.feature_packet.packet_id,
        feature_packet_sha256=contract_sha256(request.research.feature_packet),
        research_session_id=request.research.session_id,
        research_input_sha256=contract_sha256(request.research),
        research_trace_sha256=research_result_model.trace_sha256,
        pending_scope=ResearchScope.MARKET_EVIDENCE,
        pending_reason="unverified_model_proposal",
        completed_scopes=(ResearchScope.PRODUCT_TRUTH,),
        created_at=datetime.now(UTC),
    )
    continuation_result = research_result_model.model_copy(update={"continuation": continuation})
    continuation_snapshot = build_feature_launch_intent_snapshot(
        request.agent_run_id,
        continuation_result,
        resumable_scopes=("customer_intelligence",),
    )
    assert tuple(item.intent_id for item in continuation_snapshot.intents) == (
        "stop",
        "request_more_evidence",
        "propose_shadow_strategy",
    )

    class InvalidScopeJudgment:
        def run_marketing_judgment_job(
            self,
            prompt: str,
            schema: JsonObject,
            *,
            workspace: Path,
            timeout_seconds: float,
        ) -> JsonObject:
            _ = prompt, schema, workspace, timeout_seconds
            return {
                "intent_id": "request_more_evidence",
                "reason": "Request a scope that was not found insufficient.",
                "requested_scope": "market_evidence",
            }

    with pytest.raises(ValueError, match="requested scope is not insufficient"):
        _ = select_feature_launch_next_intent(
            InvalidScopeJudgment(),
            prepared=prepared,
            result=research_result_model,
            intent_snapshot=intent_snapshot_model,
            timeout_seconds=300.0,
        )
    assert result.output["effect_class"] == "none"
    assert result.output["tool_actions_created"] == 0


def test_resume_step_uses_customer_context_and_cannot_request_another_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, root_request_sha256 = resume_launch_request()
    observed: dict[str, object] = {}

    class ResumeRunner:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def run(
            self,
            research: DynamicEvidenceResearchRequest,
            *,
            capability_snapshot: ResearchCapabilitySnapshot,
        ) -> DynamicEvidenceResearchResult:
            observed["research"] = research
            receipt_chain = proof_chain(research, capability_snapshot)
            continuation = ResearchContinuation(
                schema_version="trace.research-continuation.v1",
                continuation_id="continuation-resume-two",
                account_id=research.account_id,
                feature_packet_id=research.feature_packet.packet_id,
                feature_packet_sha256=contract_sha256(research.feature_packet),
                research_session_id=research.session_id,
                research_input_sha256=contract_sha256(research),
                research_trace_sha256="3" * 64,
                pending_scope=ResearchScope.MARKET_EVIDENCE,
                pending_reason="unverified_model_proposal",
                completed_scopes=(
                    ResearchScope.PRODUCT_TRUTH,
                    ResearchScope.CUSTOMER_INTELLIGENCE,
                ),
                created_at=datetime(2026, 9, 3, tzinfo=UTC),
            )
            return DynamicEvidenceResearchResult.model_construct(
                schema_version="trace.dynamic-evidence-research-result.v4",
                session_id=research.session_id,
                state="inconclusive",
                input_snapshot_sha256=contract_sha256(research),
                registry_snapshot_sha256=contract_sha256(capability_snapshot),
                planner_protocol_sha256=capability_snapshot.planner_protocol_sha256,
                provider_id="official-codex-cli",
                model_id="gpt-test",
                trace_sha256="3" * 64,
                tool_calls=len(receipt_chain),
                spent_cost_units=sum(item.actual_cost_units for item in receipt_chain),
                capability_snapshot=capability_snapshot,
                receipt_chain=receipt_chain,
                findings=(
                    DynamicEvidenceFinding(
                        iteration=2,
                        scope=ResearchScope.CUSTOMER_INTELLIGENCE,
                        evidence_status="sufficient",
                        summary="Fresh customer context is available.",
                        source_ref="trace-marketing-context:customer-context-resume",
                        source_sha256="4" * 64,
                        trust_state="caller_supplied_projection",
                    ),
                ),
                continuation=continuation,
                evidence_brief=None,
                market_proposal=None,
            )

    def choose_proposal(
        _codex: object,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        _ = prompt, schema, workspace, timeout_seconds
        return {
            "intent_id": "propose_shadow_strategy",
            "reason": "The exact continuation is now eligible.",
            "requested_scope": None,
        }

    monkeypatch.setattr(
        "ads_booster.marketing.hosted_feature_launch_run.DynamicEvidenceResearchRunner",
        ResumeRunner,
    )
    monkeypatch.setattr(
        "ads_booster.marketing.hosted_feature_launch_run.CodexCli.run_marketing_judgment_job",
        choose_proposal,
    )
    executor = HostedFeatureLaunchRunExecutor(
        codex_executable=Path("/usr/bin/true"),
        output_root=tmp_path,
    )
    hosted_task = task(
        request,
        phase="resume",
        step_sequence=2,
        parent_step_sha256="c" * 64,
        root_request_sha256=root_request_sha256,
        resumable_scopes=(),
    )

    result = executor.execute(executor.prepare(hosted_task))

    assert observed["research"] == request.research
    assert request.research.marketing_context is not None
    assert result.output["phase"] == "resume"
    assert result.output["step_sequence"] == 2
    assert result.output["parent_step_sha256"] == "c" * 64
    assert result.output["root_request_sha256"] == root_request_sha256
    assert result.output["resumable_scopes"] == []
    snapshot = FeatureLaunchIntentSnapshot.model_validate(result.output["intent_snapshot"])
    assert tuple(item.intent_id for item in snapshot.intents) == (
        "stop",
        "propose_shadow_strategy",
    )
    decision = FeatureLaunchNextIntentDecision.model_validate(result.output["next_intent"])
    assert decision.intent_id == "propose_shadow_strategy"
    assert decision.requested_scope is None
    assert result.output["tool_actions_created"] == 0


@pytest.mark.parametrize(
    ("account_id", "credential_ref", "failure_code"),
    [
        ("other-kr", None, "feature_launch_run_scope_mismatch"),
        (
            None,
            "control-plane-secret",
            "feature_launch_run_credential_forbidden",
        ),
    ],
)
def test_hosted_feature_launch_rejects_scope_or_credentials_before_codex(
    tmp_path: Path,
    account_id: str | None,
    credential_ref: str | None,
    failure_code: str,
) -> None:
    request = launch_request()
    executor = HostedFeatureLaunchRunExecutor(
        codex_executable=Path("/usr/bin/true"),
        output_root=tmp_path,
    )

    with pytest.raises(MarketingExecutionError, match=failure_code):
        _ = executor.prepare(task(request, account_id=account_id, credential_ref=credential_ref))


@pytest.mark.parametrize(
    "mutation",
    ["snapshot_order", "control_token", "initial_root", "resume_cycle"],
)
def test_hosted_feature_launch_rejects_unbound_capability_or_secret_payload(
    tmp_path: Path,
    mutation: str,
) -> None:
    request = launch_request()
    original = task(request)
    payload = dict(original.payload)
    if mutation == "snapshot_order":
        snapshot = build_local_research_capability_snapshot(request.research.required_scopes)
        changed = snapshot.model_copy(
            update={"capabilities": tuple(reversed(snapshot.capabilities))}
        )
        payload["capability_snapshot"] = changed.model_dump(mode="json")
        payload["capability_snapshot_sha256"] = contract_sha256(changed)
    elif mutation == "control_token":
        payload["CONTROL_PLANE_TOKEN"] = "fixture"  # noqa: S105 - forbidden fixture key.
    elif mutation == "initial_root":
        payload["root_request_sha256"] = "f" * 64
    else:
        payload.update(
            phase="resume",
            step_sequence=3,
            parent_step_sha256="e" * 64,
            resumable_scopes=[],
        )
    changed_task = original.model_copy(update={"payload": payload})
    executor = HostedFeatureLaunchRunExecutor(
        codex_executable=Path("/usr/bin/true"),
        output_root=tmp_path,
    )

    with pytest.raises(MarketingExecutionError, match="feature_launch_run_payload_invalid"):
        _ = executor.prepare(changed_task)


def test_hosted_feature_launch_refuses_success_without_a_complete_receipt_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = launch_request()

    class IncompleteRunner:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def run(
            self,
            research: DynamicEvidenceResearchRequest,
            *,
            capability_snapshot: ResearchCapabilitySnapshot,
        ) -> DynamicEvidenceResearchResult:
            return DynamicEvidenceResearchResult(
                schema_version="trace.dynamic-evidence-research-result.v4",
                session_id=research.session_id,
                state="inconclusive",
                input_snapshot_sha256=contract_sha256(research),
                registry_snapshot_sha256=contract_sha256(capability_snapshot),
                planner_protocol_sha256=capability_snapshot.planner_protocol_sha256,
                provider_id="official-codex-cli",
                model_id="gpt-test",
                trace_sha256="3" * 64,
                tool_calls=0,
                spent_cost_units=0,
                capability_snapshot=capability_snapshot,
                receipt_chain=(),
                findings=(),
            )

    monkeypatch.setattr(
        "ads_booster.marketing.hosted_feature_launch_run.DynamicEvidenceResearchRunner",
        IncompleteRunner,
    )
    executor = HostedFeatureLaunchRunExecutor(
        codex_executable=Path("/usr/bin/true"),
        output_root=tmp_path,
    )

    with pytest.raises(
        MarketingExecutionError,
        match="feature_launch_research_receipt_chain_incomplete",
    ):
        _ = executor.execute(executor.prepare(task(request)))


def test_hosted_feature_launch_fails_closed_when_next_intent_provider_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = launch_request()

    class CompleteRunner:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def run(
            self,
            research: DynamicEvidenceResearchRequest,
            *,
            capability_snapshot: ResearchCapabilitySnapshot,
        ) -> DynamicEvidenceResearchResult:
            receipt_chain = proof_chain(research, capability_snapshot)
            return DynamicEvidenceResearchResult(
                schema_version="trace.dynamic-evidence-research-result.v4",
                session_id=research.session_id,
                state="inconclusive",
                input_snapshot_sha256=contract_sha256(research),
                registry_snapshot_sha256=contract_sha256(capability_snapshot),
                planner_protocol_sha256=capability_snapshot.planner_protocol_sha256,
                provider_id="official-codex-cli",
                model_id="gpt-test",
                trace_sha256="3" * 64,
                tool_calls=len(receipt_chain),
                spent_cost_units=sum(item.actual_cost_units for item in receipt_chain),
                capability_snapshot=capability_snapshot,
                receipt_chain=receipt_chain,
                findings=(),
            )

    def fail_intent(*_args: object, **_kwargs: object) -> None:
        message = "fixture_next_intent_failure"
        raise CodexCliError(message)

    monkeypatch.setattr(
        "ads_booster.marketing.hosted_feature_launch_run.DynamicEvidenceResearchRunner",
        CompleteRunner,
    )
    monkeypatch.setattr(
        "ads_booster.marketing.hosted_feature_launch_run.select_feature_launch_next_intent",
        fail_intent,
    )
    executor = HostedFeatureLaunchRunExecutor(
        codex_executable=Path("/usr/bin/true"),
        output_root=tmp_path,
    )

    with pytest.raises(MarketingExecutionError, match="feature_launch_next_intent_failed"):
        _ = executor.execute(executor.prepare(task(request)))
