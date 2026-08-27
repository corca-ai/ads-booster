from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter, ValidationError

from ads_booster.candidate_generation.errors import CandidateFormatError
from ads_booster.candidate_generation.models import CandidateDraft
from ads_booster.transport.json_types import JsonValue

if TYPE_CHECKING:
    from ads_booster.workspace import CandidatePersonaDomain

_DRAFTS: TypeAdapter[tuple[CandidateDraft, ...]] = TypeAdapter(tuple[CandidateDraft, ...])
_JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_FENCE: Final = "```"
_MAX_DETAIL_CHARS: Final = 500


def parse_candidate_drafts(
    text: str,
    *,
    expected: int,
    country: str,
    domains: tuple[CandidatePersonaDomain, ...] | None = None,
) -> tuple[CandidateDraft, ...]:
    """Parse the strict JSON array a generation call must return.

    Raises `CandidateFormatError` with a detail the retry turn can quote back.
    """
    payload = strip_json_fence(text)
    if not payload:
        empty = "응답이 비어 있습니다."
        raise CandidateFormatError(empty)
    try:
        parsed = _JSON_VALUE.validate_json(payload)
    except ValidationError as error:
        invalid_json = f"JSON 파싱 실패: {_detail(error)}"
        raise CandidateFormatError(invalid_json) from error
    if not isinstance(parsed, list):
        not_array = "최상위 값이 JSON 배열이 아닙니다."
        raise CandidateFormatError(not_array)
    if len(parsed) != expected:
        wrong_length = f"후보 {expected}개가 필요하지만 {len(parsed)}개를 받았습니다."
        raise CandidateFormatError(wrong_length)
    try:
        drafts = _DRAFTS.validate_python(parsed)
    except ValidationError as error:
        raise CandidateFormatError(_detail(error)) from error
    wrong_country = [draft.country for draft in drafts if draft.country != country]
    if wrong_country:
        mismatch = f"country는 모두 {country}여야 합니다: {wrong_country}"
        raise CandidateFormatError(mismatch)
    if domains is not None:
        _require_assigned_domains(drafts, domains)
    return drafts


def _require_assigned_domains(
    drafts: tuple[CandidateDraft, ...],
    domains: tuple[CandidatePersonaDomain, ...],
) -> None:
    """Hold the batch to the 1:1 domain assignment it was given.

    The binding is positional on purpose: a model told "cover these three domains" will
    happily return three candidates in its favourite one and call the set covered.
    """
    written = tuple(draft.persona_domain for draft in drafts)
    if written != domains:
        expected = ", ".join(domain.value for domain in domains)
        received = ", ".join("(없음)" if domain is None else domain.value for domain in written)
        mismatch = (
            f"persona_domain은 배정된 순서대로 [{expected}] 여야 하지만 [{received}] 를 받았습니다."
        )
        raise CandidateFormatError(mismatch)


def strip_json_fence(text: str) -> str:
    """Remove a surrounding markdown code fence when the model wrapped its JSON."""
    stripped = text.strip()
    if not stripped.startswith(_FENCE):
        return stripped
    without_open = stripped[len(_FENCE) :]
    newline = without_open.find("\n")
    if newline == -1:
        return ""
    body = without_open[newline + 1 :]
    close = body.rfind(_FENCE)
    return body[:close].strip() if close != -1 else body.strip()


def _detail(error: ValidationError) -> str:
    lines = [
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in error.errors()[:5]
    ]
    return "; ".join(lines)[:_MAX_DETAIL_CHARS]
