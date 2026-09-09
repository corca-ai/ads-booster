from __future__ import annotations

from hashlib import sha256


def stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x00".join(parts).encode()).hexdigest()
    return f"{prefix}.{digest[:32]}"


__all__ = ["stable_id"]
