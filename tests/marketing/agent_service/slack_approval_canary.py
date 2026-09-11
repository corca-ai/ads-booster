"""Opt-in installed actual-model approval rehearsal; every external adapter is synthetic."""

from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.contracts.agent_run import AgentRecordKind, contract_sha256
from ads_booster.contracts.tool_capability import ToolExecutionResult
from ads_booster.providers.codex_cli import CodexCli
from ads_booster.providers.codex_reasoning import CodexReasoningProvider
from ads_booster.tools.descriptors import github_issue_descriptor, image_generation_descriptor
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events

if TYPE_CHECKING:
    from ads_booster.contracts.agent_run import ToolInvocation
    from ads_booster.contracts.tool_capability import ToolDescriptor
    from ads_booster.transport.json_types import JsonObject


SCENARIOS = (
    (
        "direct-image",
        "Trace 한국용 홍보 이미지 한 장 귀여운 컨셉으로 만들어줘. 색과 구도는 네가 정해.",
        "creative.image.generate",
    ),
    (
        "direct-issue",
        (
            "승인 대기 중 '읽었어'라고 하면 완료로 바뀌어서 이미지 생성이 안 되는 버그가 있어. "
            "이거 깃허브 이슈로 올려봐."
        ),
        "github.issue.create",
    ),
    ("question", "깃허브 이슈도 올릴 수 있어? 어떤 식으로 동작하는지 설명해줘.", None),
    (
        "draft-only",
        "승인 대기 버그를 설명하는 깃허브 이슈 제목과 본문 초안만 써줘. 아직 등록하지 마.",
        None,
    ),
    ("negated", "이미지는 만들지 말고 귀여운 홍보 이미지에 쓸 문구만 세 개 줘.", None),
    (
        "quoted",
        "동료가 '이거 깃허브 이슈로 올려봐'라고 했는데 무슨 뜻인지 설명해줘. 실행 요청은 아니야.",
        None,
    ),
)


class SyntheticCreation:
    def __init__(self, root: Path) -> None:
        self.calls: list[str] = []
        self.root: Path = root

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> ToolExecutionResult:
        self.calls.append(descriptor.capability_id)
        image = self.root / "fixture.png"
        Image.new("RGB", (128, 128), "blue").save(image)
        output: JsonObject = {
            "artifact_sha256": sha256(image.read_bytes()).hexdigest(),
            "width": 128,
            "height": 128,
            "media_type": "image/png",
            "review_status": "awaiting_human_review",
            "prompt_sha256": sha256(str(invocation.input.get("prompt", "")).encode()).hexdigest(),
            "invocation_sha256": contract_sha256(invocation),
        }
        if descriptor.capability_id == "github.issue.create":
            output = {
                "repository": "corca-ai/ads-booster",
                "number": 123,
                "url": "https://github.com/corca-ai/ads-booster/issues/123",
            }
        return ToolExecutionResult(
            schema_version="trace.tool-execution-result.v1",
            disposition="succeeded",
            invocation_sha256=contract_sha256(invocation),
            output=output,
            actual_cost_units=1,
            executor_id="synthetic-creation",
        )


class Arguments(argparse.Namespace):
    output_root: Path = Path()
    codex: Path = Path()
    model: str = ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--output-root", type=Path, required=True)
    _ = parser.add_argument("--codex", type=Path, required=True)
    _ = parser.add_argument("--model", required=True)
    args = parser.parse_args(namespace=Arguments())
    args.output_root.mkdir(parents=True, exist_ok=False)
    _ = (args.output_root / "criteria.json").write_text(json.dumps(SCENARIOS, ensure_ascii=False))
    results: list[JsonObject] = []
    for name, message, expected in SCENARIOS:
        root = args.output_root / name
        root.mkdir()
        owner, replies = setup_events(root)
        service = owner.commands.application.service
        service.reasoning = CodexReasoningProvider(
            CodexCli(args.codex, model=args.model),
            root / "model",
            model_id=args.model,
        )
        service.registry = ToolRegistry(
            (
                image_generation_descriptor(observed_at=NOW),
                github_issue_descriptor(
                    installation_id="synthetic-github", observed_at=NOW, ready=True
                ),
            )
        )
        adapter = SyntheticCreation(root)
        service.tools = {"creative.image.generate": adapter, "github.issue.create": adapter}
        receive(owner, text=f"<@UBOT> {message}")
        assert owner.work_once(now=NOW)
        run = service.repository.list_runs("team")[0]
        records = service.repository.records("team", run.run_id)
        restarted = SlackEvents(owner.commands, "UBOT", frozenset({"C1"}))
        restarted.recover()
        receive(restarted, text=f"<@UBOT> {message}")
        assert not restarted.work_once(now=NOW)
        result: JsonObject = {
            "scenario": name,
            "message": message,
            "expected": expected,
            "actual_calls": list(adapter.calls),
            "state": run.state.value,
            "reply": replies[-1]["text"],
            "boundary_passed": adapter.calls == ([] if expected is None else [expected])
            and run.state.value == "completed",
            "decisions": [r.payload for r in records if r.kind is AgentRecordKind.REASONING],
            "approvals": [r.payload for r in records if r.kind is AgentRecordKind.APPROVAL],
        }
        results.append(result)
        _ = (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(  # noqa: T201 - opt-in operator evidence stream.
            json.dumps(
                {"scenario": name, "passed": result["boundary_passed"], "reply": result["reply"]},
                ensure_ascii=False,
            ),
            flush=True,
        )
    _ = (args.output_root / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2)
    )
    if not all(r["boundary_passed"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
