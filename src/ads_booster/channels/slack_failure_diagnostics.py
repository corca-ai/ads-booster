"""Bounded failure locations without exception messages, source text or local values."""

from __future__ import annotations

import re

_MAX_CAUSES = 4
_MAX_FRAMES = 4

_SAFE_TYPES = frozenset(
    {
        "CodexReasoningError",
        "RuntimeError",
        "ValueError",
        "ValidationError",
        "TypeError",
        "KeyError",
        "OSError",
        "TimeoutError",
        "TimeoutExpired",
        "ConnectionError",
        "PermissionError",
        "FileNotFoundError",
    }
)


def failure_diagnostic(error: BaseException) -> str:
    """Record only product code locations, including wrapped causes, for the journal."""
    causes: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(causes) < _MAX_CAUSES:
        seen.add(id(current))
        name = type(current).__name__
        category = name if name in _SAFE_TYPES else "Exception"
        frames: list[str] = []
        trace = current.__traceback__
        while trace is not None:
            module = trace.tb_frame.f_globals.get("__name__")
            function = trace.tb_frame.f_code.co_name
            if (
                isinstance(module, str)
                and re.fullmatch(r"ads_booster\.[a-zA-Z0-9_.]+", module)
                and re.fullmatch(r"[a-zA-Z0-9_]+", function)
            ):
                frames.append(f"{module}.{function}:{trace.tb_lineno}")
            trace = trace.tb_next
        causes.append(category + "@" + (",".join(frames[-_MAX_FRAMES:]) or "external"))
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return " <- ".join(causes)
