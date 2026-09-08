"""Project verified GitHub issue receipts into Slack replies without model inference."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import AgentRecordKind, contract_sha256
from ads_booster.marketing.agent_service.github_issues import CAPABILITY, REPOSITORY

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ads_booster.contracts.agent_run import AgentRecord


def issue_results(records: Sequence[AgentRecord]) -> str:
    receipts = {
        contract_sha256(record.payload): record.payload
        for record in records
        if record.kind is AgentRecordKind.RECEIPT
    }
    lines: list[str] = []
    for record in records:
        if (
            record.kind is not AgentRecordKind.EVIDENCE
            or record.payload.get("capability_id") != CAPABILITY
        ):
            continue
        receipt = receipts.get(str(record.payload.get("receipt_sha256")))
        output = record.payload.get("output")
        if (
            receipt is None
            or receipt.get("disposition") != "succeeded"
            or not isinstance(output, dict)
        ):
            continue
        if receipt.get("output_sha256") != contract_sha256(output):
            continue
        number = output.get("number")
        if type(number) is not int or number < 1:
            continue
        url = f"https://github.com/{REPOSITORY}/issues/{number}"
        if output.get("repository") == REPOSITORY and output.get("url") == url:
            lines.append(f"GitHub 이슈 생성 완료: {url}")
    return "\n".join(dict.fromkeys(lines))
