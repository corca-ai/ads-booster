from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.tools._http_json import HttpResponse, post_json
from ads_booster.tools.compatibility import DelegatedToolResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from ads_booster.contracts.agent_run import ToolInvocation
    from ads_booster.contracts.tool_capability import ToolDescriptor


def execute_slack_delivery(
    invocation: ToolInvocation,
    descriptor: ToolDescriptor,
    *,
    token: str,
    channel_id: str,
    opener: Callable[..., HttpResponse],
) -> DelegatedToolResult:
    _ = descriptor
    text = invocation.input.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("slack_delivery_text_required")
    output = post_json(
        opener,
        "https://slack.com/api/chat.postMessage",
        {"channel": channel_id, "text": text},
        {"authorization": f"Bearer {token}"},
    )
    if output.get("ok") is not True:
        raise ValueError("slack_delivery_rejected")
    return DelegatedToolResult(disposition="succeeded", output=output, actual_cost_units=1)
