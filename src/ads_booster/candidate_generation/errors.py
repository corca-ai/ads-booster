from __future__ import annotations

from typing import TYPE_CHECKING, final, override

if TYPE_CHECKING:
    from pathlib import Path

_RUN_FROM_TRACE_FOLDER = "trace 폴더에서 서버를 실행했는지 확인하세요."


class CandidateGenerationError(RuntimeError):
    """Base class for failures that stop an automatic candidate run.

    Every subclass carries a Korean `message` that is safe to show to the operator
    verbatim; the caller maps the type to a failure code and passes the text through.
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
class CandidateReferencesMissingError(CandidateGenerationError):
    def __init__(self, directory: Path, country: str) -> None:
        """Create an error for a country the reference corpus does not cover.

        Falling back to another country's corpus would be worse than failing: the batch
        would be written from Korean posts and labelled as the other country's, and nothing
        downstream would say so. The corpus is KR-only today, so this is the wall a second
        country hits first, by design.
        """
        self.directory = directory
        self.country = country
        message = (
            f"{country} 레퍼런스가 없습니다 (경로: {directory}) — "
            f"이 국가의 레퍼런스를 먼저 추가해야 후보를 만들 수 있습니다."
        )
        super().__init__(message)


@final
class CandidateProviderError(CandidateGenerationError):
    def __init__(self, provider_code: str | None = None) -> None:
        """Create an error for a provider call that did not return a usable response."""
        self.provider_code = provider_code
        safe_code = provider_code or "provider_error"
        super().__init__(f"AI 요청에 실패했습니다 ({safe_code}) — 잠시 후 다시 시도해 주세요.")


@final
class CandidateFormatError(CandidateGenerationError):
    def __init__(self, detail: str) -> None:
        """Create an error for a model response that failed the strict output contract."""
        self.detail = detail
        super().__init__("AI 응답이 형식을 통과하지 못했습니다 — 다시 시도해 주세요.")
