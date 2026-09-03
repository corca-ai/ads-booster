"""Compatibility adapters that expose existing automation as Agent Core tools."""

from ads_booster.marketing.tool_adapters.compatibility import (
    DelegatedToolResult,
    DelegatingToolAdapter,
    ToolDelegationError,
    ToolExecutor,
    appium_adapter,
    candidate_adapter,
    capture_adapter,
    research_adapter,
    threads_adapter,
)
from ads_booster.marketing.tool_adapters.descriptors import (
    appium_descriptor,
    candidate_descriptor,
    capture_descriptor,
    research_descriptor,
    threads_descriptor,
)

__all__ = [
    "DelegatedToolResult",
    "DelegatingToolAdapter",
    "ToolDelegationError",
    "ToolExecutor",
    "appium_adapter",
    "appium_descriptor",
    "candidate_adapter",
    "candidate_descriptor",
    "capture_adapter",
    "capture_descriptor",
    "research_adapter",
    "research_descriptor",
    "threads_adapter",
    "threads_descriptor",
]
