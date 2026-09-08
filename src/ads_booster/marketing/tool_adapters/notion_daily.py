from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.marketing.tool_adapters._http_json import HttpResponse, post_json
from ads_booster.marketing.tool_adapters.compatibility import DelegatedToolResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from ads_booster.contracts.agent_run import ToolInvocation
    from ads_booster.contracts.tool_capability import ToolDescriptor


def execute_notion_daily(
    invocation: ToolInvocation,
    descriptor: ToolDescriptor,
    *,
    token: str,
    parent_page_id: str,
    opener: Callable[..., HttpResponse],
) -> DelegatedToolResult:
    _ = descriptor
    title = invocation.input.get("title")
    content = invocation.input.get("content")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("notion_daily_title_required")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("notion_daily_content_required")
    output = post_json(
        opener,
        "https://api.notion.com/v1/pages",
        {
            "parent": {"page_id": parent_page_id},
            "properties": {"title": {"title": [{"text": {"content": title}}]}},
            "children": [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": content}}]
                    },
                }
            ],
        },
        {
            "authorization": f"Bearer {token}",
            "notion-version": "2022-06-28",
        },
    )
    return DelegatedToolResult(disposition="succeeded", output=output, actual_cost_units=1)
