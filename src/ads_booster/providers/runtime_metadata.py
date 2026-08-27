from __future__ import annotations

import json
from typing import Final

_RUNTIME_METADATA_OPEN: Final = "<trace-agent-runtime>"
_RUNTIME_METADATA_CLOSE: Final = "</trace-agent-runtime>"
_RUNTIME_DISCLOSURE_INSTRUCTION: Final = (
    "The tagged trace-agent runtime block is authoritative for this request. "
    "When asked which model Trace is running, report its requested_model value "
    "and do not claim that runtime metadata is unavailable."
)


def instructions_with_runtime(instructions: str, model: str) -> str:
    metadata = json.dumps(
        {"requested_model": model},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "\n\n".join(
        (
            instructions,
            _RUNTIME_DISCLOSURE_INSTRUCTION,
            f"{_RUNTIME_METADATA_OPEN}{metadata}{_RUNTIME_METADATA_CLOSE}",
        )
    )
