"""Compatibility adapters that expose existing automation as Agent Core tools."""

from ads_booster.tools.compatibility import (
    DelegatedToolResult,
    DelegatingToolAdapter,
    ToolDelegationError,
    ToolExecutor,
    research_adapter,
)
from ads_booster.tools.descriptors import (
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
