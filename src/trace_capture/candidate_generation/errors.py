from __future__ import annotations

from typing import TYPE_CHECKING, final, override

if TYPE_CHECKING:
    from pathlib import Path

_RUN_FROM_TRACE_FOLDER = "trace 폴더에서 서버를 실행했는지 확인하세요."


class CandidateGenerationError(RuntimeError):
    """Base class for failures that stop an automatic candidate run.

    Every subclass carries a Korean `message` that is safe to show to the operator
    verbatim; the Web layer maps the type to a status code and passes the text through.
    """

    message: str

    def __init__(self, message: str) -> None:
        """Create an error whose message is already user-facing Korean."""
        super().__init__(message)
        self.message = message

    @override
    def __str__(self) -> str:
        return self.message


@final
class CandidateContextMissingError(CandidateGenerationError):
    def __init__(self, directory: Path, missing: tuple[str, ...] = ()) -> None:
        """Create an error for an absent context directory or absent context files."""
        self.directory = directory
        self.missing = missing
        if missing:
            listed = ", ".join(missing)
            message = (
                f"context 파일을 읽을 수 없습니다: {listed} "
                f"(경로: {directory}) — {_RUN_FROM_TRACE_FOLDER}"
            )
        else:
            message = (
                f"context 폴더를 찾을 수 없습니다 (경로: {directory}) — {_RUN_FROM_TRACE_FOLDER}"
            )
        super().__init__(message)


@final
class CandidateAuthRequiredError(CandidateGenerationError):
    def __init__(self) -> None:
        """Create an error for a missing or unusable model-provider credential."""
        super().__init__(
            "AI 로그인이 필요합니다 — 터미널에서 `trace-agent auth login`을 실행하세요."
        )


@final
class CandidateProviderError(CandidateGenerationError):
    def __init__(self, *, context_overflow: bool = False) -> None:
        """Create an error for a provider call that did not return a usable response."""
        self.context_overflow = context_overflow
        message = (
            "context 파일이 너무 커서 한 번에 보낼 수 없습니다 — context 문서를 줄여 주세요."
            if context_overflow
            else "AI 요청에 실패했습니다 — 잠시 후 다시 시도해 주세요."
        )
        super().__init__(message)


@final
class CandidateFormatError(CandidateGenerationError):
    def __init__(self, detail: str) -> None:
        """Create an error for a model response that failed the strict output contract."""
        self.detail = detail
        super().__init__("AI 응답이 형식을 통과하지 못했습니다 — 다시 시도해 주세요.")
