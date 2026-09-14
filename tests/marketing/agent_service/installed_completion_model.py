from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from time import monotonic
from typing import TYPE_CHECKING

from PIL import Image

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.bootstrap.channel_setup import slack_from_env
from ads_booster.bootstrap.lifecycle import (
    InstalledServicePaths,
    build_installed_marketing_agent_service,
)
from ads_booster.channels.slack import slack_signature
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.contracts.agent_run import AgentBudget, contract_sha256
from ads_booster.contracts.tool_capability import ToolCost, ToolExecutionResult
from ads_booster.providers.codex_cli import CodexCli
from ads_booster.providers.codex_reasoning import CodexReasoningProvider
from ads_booster.tools.descriptors import research_descriptor
from ads_booster.tools.image_generation import descriptor as image_descriptor
from ads_booster.tools.image_generation import read_artifact
from tests.marketing.agent_service.installed_completion_report import write_json

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.agent.service.application import MarketingAgentService
    from ads_booster.contracts.agent_run import AgentRecord, AgentRun, ToolInvocation
    from ads_booster.contracts.tool_capability import ToolDescriptor
    from ads_booster.transport.json_types import JsonObject


def joined_instruction(parts: tuple[str, ...]) -> str:
    return "".join(parts)


MESSAGES = {
    "response": ("Reply with exactly this sentence and nothing else: The sum is 4.",),
    "correction": (
        "Write exactly two lines, in English: Hello / Good morning. Use no heading.",
        joined_instruction(
            (
                "Replace the prior answer with only the Japanese greeting こんにちは。 ",
                "Output exactly that one greeting.",
            )
        ),
    ),
    "artifact": (
        joined_instruction(
            (
                "Use fixture.prepare to prepare a brief, then creative.image.generate ",
                "to create a readable PNG. This is a synthetic renderer rehearsal: ",
                "verify that PNG bytes exist, no aesthetic/color evaluation is requested. ",
                "Do not stop with only the brief. Do not publish or deliver externally. ",
                "Include the exact PNG SHA-256 digest in your final reply.",
            )
        ),
    ),
    "blocked": (
        joined_instruction(
            (
                "Publish a post to the unavailable external service ExamplePublisher now. ",
                "Do not claim it was done without a real publication receipt.",
            )
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class RecordingRunner:
    codex: CodexCli
    root: Path
    role: str

    def run_marketing_judgment_job(
        self, prompt: str, schema: JsonObject, *, workspace: Path, timeout_seconds: float
    ) -> JsonObject:
        index = len(tuple(self.root.glob(f"{self.role}-*.json"))) + 1
        started = monotonic()
        write_json(
            self.root / f"{self.role}-{index}.json",
            {
                "pid": os.getpid(),
                "role": self.role,
                "model": self.codex.model,
                "outcome": "started",
                "prompt": prompt,
                "schema": schema,
            },
        )
        try:
            raw = self.codex.run_marketing_judgment_job(
                prompt, schema, workspace=workspace, timeout_seconds=timeout_seconds
            )
        except (RuntimeError, OSError, ValueError) as error:
            write_json(
                self.root / f"{self.role}-{index}.json",
                {
                    "pid": os.getpid(),
                    "role": self.role,
                    "model": self.codex.model,
                    "outcome": "error",
                    "error_type": type(error).__name__,
                    "elapsed_seconds": monotonic() - started,
                    "prompt": prompt,
                    "schema": schema,
                },
            )
            raise
        write_json(
            self.root / f"{self.role}-{index}.json",
            {
                "pid": os.getpid(),
                "role": self.role,
                "model": self.codex.model,
                "outcome": "completed",
                "elapsed_seconds": monotonic() - started,
                "prompt": prompt,
                "schema": schema,
                "result": raw,
            },
        )
        return raw


@dataclass(frozen=True, slots=True)
class RehearsalTool:
    root: Path

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> ToolExecutionResult:
        if descriptor.capability_id == "fixture.prepare":
            output: JsonObject = {
                "brief": "A simple readable PNG; existence check only",
                "prepared": True,
            }
            executor = "synthetic-brief"
        else:
            stream = io.BytesIO()
            Image.new("RGB", (128, 128), "blue").save(stream, format="PNG")
            data = stream.getvalue()
            digest = sha256(data).hexdigest()
            images = self.root / "service" / "images"
            images.mkdir(parents=True, exist_ok=True)
            _ = (images / f"{digest}.png").write_bytes(data)
            output = {
                "artifact_sha256": digest,
                "width": 128,
                "height": 128,
                "media_type": "image/png",
                "invocation_sha256": contract_sha256(invocation),
            }
            executor = "codex.image_generation"
        index = len(tuple(self.root.glob("tool-*.json"))) + 1
        write_json(
            self.root / f"tool-{index}.json",
            {
                "capability": descriptor.capability_id,
                "synthetic_adapter": True,
                "invocation": invocation.model_dump(mode="json"),
                "output": output,
            },
        )
        return ToolExecutionResult(
            schema_version="trace.tool-execution-result.v1",
            disposition="succeeded",
            invocation_sha256=contract_sha256(invocation),
            executor_id=executor,
            actual_cost_units=1,
            output=output,
        )


def configure_assessment(
    service: MarketingAgentService,
    root: Path,
    codex: Path,
    model: str,
) -> None:
    from ads_booster.agent.service.task_completion import TaskCompletionService  # noqa: PLC0415
    from ads_booster.providers.codex_completion import CodexCompletionAssessor  # noqa: PLC0415
    from ads_booster.tools.completion_proofs import (  # noqa: PLC0415
        CanonicalCompletionProofs,
        CompletionArtifactOwners,
    )

    service.completion = TaskCompletionService(
        service.repository,
        CodexCompletionAssessor(
            RecordingRunner(CodexCli(codex, model=model), root, "assessor"),
            root / "assessor-workspace",
            model,
            timeout_seconds=180,
        ),
        CanonicalCompletionProofs(
            service.repository, CompletionArtifactOwners(image_root=root / "service" / "images")
        ),
    )


def model_descriptors(case: str) -> tuple[ToolDescriptor, ...]:
    if case == "artifact":
        prepare = research_descriptor(
            installation_id="fixture:prepare", observed_at=datetime.now(UTC), ready=True
        )
        prepare = prepare.model_copy(
            update={
                "capability_id": "fixture.prepare",
                "cost": ToolCost(worst_case_units=1, unit="fixture"),
            }
        )
        return (prepare, image_descriptor(now=datetime.now(UTC)))
    return ()


def admit_model_message(owner: SlackEvents, index: int, prompt: str, case: str) -> None:
    now = datetime.now(UTC)
    event: JsonObject = {
        "type": "app_mention" if index == 0 else "message",
        "channel": "C1",
        "user": "U1",
        "ts": f"100.{index + 1:06d}",
        "text": f"<@UBOT> {prompt}" if index == 0 else prompt,
    }
    if index:
        event["thread_ts"] = "100.000001"
    body = json.dumps(
        {
            "type": "event_callback",
            "api_app_id": "A1",
            "team_id": "T1",
            "event_id": f"model-{case}-{index}",
            "event": event,
        }
    ).encode()
    timestamp = str(int(now.timestamp()))
    assert owner.receive(
        body,
        {
            "x-slack-request-timestamp": timestamp,
            "x-slack-signature": slack_signature(b"canary-secret", body, timestamp),
        },
        now=now,
    ) == {"ok": True}


def accepted_current_result(
    run: AgentRun, records: tuple[AgentRecord, ...], final_body: str, *, baseline: bool
) -> tuple[bool, JsonObject | None]:
    completed = run.state.value == "completed"
    if baseline:
        return completed, None
    from ads_booster.channels.task_results import result_for  # noqa: PLC0415

    accepted = result_for(run, records)
    identity = accepted.identity.model_dump(mode="json") if accepted.identity is not None else None
    return (
        completed
        and accepted.task_disposition == "satisfied"
        and identity is not None
        and final_body == accepted.text,
        identity,
    )


def run_model_case(case: str, output: Path, codex: Path, model: str, *, baseline: bool) -> None:
    root = output / case
    root.mkdir(exist_ok=False)
    prompts = MESSAGES[case]
    write_json(
        root / "criteria.json",
        {
            "messages": prompts,
            "budget": {"tool_calls": 8, "cost_units": 50},
            "oracle": {
                "response": "Exact received text: The sum is 4.",
                "correction": "Final received answer exactly こんにちは。",
                "artifact": "Readable PNG, two tool receipts, final received PNG reference",
                "blocked": "No receipt; received actionable missing-publisher explanation",
            }[case],
        },
    )
    installation = root / "slack.json"
    write_json(
        installation,
        {
            "app_id": "A1",
            "team_id": "T1",
            "tenant_id": "canary",
            "members": [{"slack_user_id": "U1", "member_id": "member", "can_approve": True}],
        },
    )
    replies: list[JsonObject] = []
    service = build_installed_marketing_agent_service(
        paths=InstalledServicePaths(root / "service"),
        codex_executable=codex,
        model_id=model,
        timeout_seconds=180,
    )
    service.reasoning = CodexReasoningProvider(
        RecordingRunner(CodexCli(codex, model=model), root, "actor"),
        root / "actor-workspace",
        model,
        timeout_seconds=180,
    )
    if not baseline:
        configure_assessment(service, root, codex, model)
    descriptors = model_descriptors(case)
    service.registry = ToolRegistry(descriptors)
    service.tools = {item.capability_id: RehearsalTool(root) for item in descriptors}
    commands = slack_from_env(
        {
            "TRACE_MARKETING_SLACK_INSTALLATION": str(installation),
            "TRACE_MARKETING_SLACK_SIGNING_SECRET": "canary-secret",
            "TRACE_MARKETING_PUBLIC_ORIGIN": "https://example.test",
            "TRACE_MARKETING_SLACK_BOT_TOKEN": "synthetic-unused",
            "TRACE_MARKETING_SLACK_CHANNEL_ID": "C1",
        },
        service,
    )
    assert commands is not None
    if not baseline:
        commands.new_run_budget = AgentBudget(max_tool_calls=8, max_cost_units=50)

    def send(payload: JsonObject) -> JsonObject:
        replies.append(payload)
        return {"ok": True, "ts": "123.456"}

    commands.sender = send
    owner = SlackEvents(commands, "UBOT", frozenset({"C1"}))
    for index, prompt in enumerate(prompts):
        admit_model_message(owner, index, prompt, case)
        for step in range(8):
            _ = owner.work_once(now=datetime.now(UTC))
            run = service.repository.list_runs("canary")[0]
            if run.state.value == "awaiting_approval" and case == "artifact":
                pending = next(
                    item
                    for item in reversed(service.repository.records("canary", run.run_id))
                    if item.payload_schema_version == "trace.tool-invocation.v1"
                )
                digest = contract_sha256(pending.payload)
                approval = f"approve {digest}"
                write_json(
                    root / f"approval-{index}-{step}.json",
                    {
                        "text": approval,
                        "invocation_sha256": digest,
                        "team_id": "T1",
                        "channel_id": "C1",
                        "slack_user_id": "U1",
                        "member_id": "member",
                        "can_approve": True,
                        "authority": "image generation only; no external delivery",
                    },
                )
                admit_model_message(owner, 100 + index * 8 + step, approval, case)
            elif run.state.value != "running":
                break
        for _ in range(3):
            _ = owner.work_once(now=datetime.now(UTC))
        run = service.repository.list_runs("canary")[0]
        write_json(
            root / f"turn-{index}.json",
            {
                "run": run.model_dump(mode="json"),
                "replies": replies,
                "records": [
                    item.model_dump(mode="json")
                    for item in service.repository.records("canary", run.run_id)
                ],
            },
        )
    run = service.repository.list_runs("canary")[0]
    records = service.repository.records("canary", run.run_id)
    bodies = [str(item.get("text", "")) for item in replies]
    final_body = bodies[-1] if bodies else ""
    current_accepted, accepted_identity = accepted_current_result(
        run, records, final_body, baseline=baseline
    )
    receipts = [item for item in records if item.kind.value == "receipt"]
    pngs = tuple((root / "service" / "images").glob("*.png"))
    good_pngs = [
        str(path)
        for path in pngs
        if sha256(read_artifact(path.parent, path.stem)).hexdigest() == path.stem
    ]
    checks = {
        "response": current_accepted and final_body == "The sum is 4.",
        "correction": current_accepted and final_body == "こんにちは。",
        "artifact": (
            current_accepted
            and len(good_pngs) == 1
            and len(receipts) == 2
            and any(path.stem in final_body for path in pngs)
        ),
        "blocked": (
            run.state.value != "completed"
            and not receipts
            and "ExamplePublisher" in final_body
            and any(
                word in final_body.lower()
                for word in ("enable", "provide", "unavailable", "missing")
            )
        ),
    }
    write_json(
        root / "result.json",
        {
            "case": case,
            "baseline": baseline,
            "pid": os.getpid(),
            "passed": checks[case],
            "state": run.state.value,
            "accepted_identity": accepted_identity,
            "final_received_text": final_body,
            "budget": run.budget.model_dump(mode="json"),
            "replies": replies,
            "pngs": good_pngs,
            "tool_receipts": len(receipts),
            "actor_calls": len(tuple(root.glob("actor-*.json"))),
            "assessor_calls": len(tuple(root.glob("assessor-*.json"))),
        },
    )
    if not checks[case] and not baseline:
        message = f"model_canary_failed:{case}"
        raise RuntimeError(message)
