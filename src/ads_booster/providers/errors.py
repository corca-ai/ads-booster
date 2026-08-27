from __future__ import annotations

from typing import override


class ProviderError(RuntimeError):
    code: str
    message: str
    context_overflow: bool

    def __init__(self, code: str, message: str, *, context_overflow: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.context_overflow = context_overflow

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.message}"
