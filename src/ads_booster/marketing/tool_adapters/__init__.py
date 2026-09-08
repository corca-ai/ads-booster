"""Compatibility adapters that expose existing automation as Agent Core tools."""

from ads_booster.marketing.tool_adapters.compatibility import (
    DelegatedToolResult,
    DelegatingToolAdapter,
    ToolDelegationError,
    ToolExecutor,
    research_adapter,
)
from ads_booster.marketing.tool_adapters.descriptors import (
    research_descriptor,
)

__all__ = [
    "DelegatedToolResult",
    "DelegatingToolAdapter",
    "ToolDelegationError",
    "ToolExecutor",
    "research_adapter",
    "research_descriptor",
]
