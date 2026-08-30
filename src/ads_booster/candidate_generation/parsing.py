from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter, ValidationError

from ads_booster.candidate_generation.errors import CandidateFormatError
from ads_booster.candidate_generation.models import CandidateDraft

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonValue
    from ads_booster.workspace import CandidatePersonaDomain

CANDIDATES_KEY: Final = "candidates"

_DRAFTS: TypeAdapter[tuple[CandidateDraft, ...]] = TypeAdapter(tuple[CandidateDraft, ...])
_MAX_DETAIL_CHARS: Final = 500


def parse_candidate_drafts(
    payload: JsonValue,
    *,
    expected: int,
    country: str,
    domains: tuple[CandidatePersonaDomain, ...] | None = None,
) -> tuple[CandidateDraft, ...]:
    """Parse the structured envelope one generation call must return.

    The provider is handed an output schema, so a malformed answer is rarer than it was on
    the free-text path — but "schema-shaped" and "usable" are different questions. A schema
    cannot say that the country has to match the request, or that a candidate has to carry
    the domain it was assigned, and those are the two ways a batch quietly stops being what
    was asked for.

    A short answer is accepted rather than thrown away. The whole batch is one call now, so
    rejecting three good candidates because a fourth is missing would cost the request
    everything to punish the model for a shortfall the caller can simply report. More than
    was asked for is still a failure: the extra candidate has no assignment behind it.

    Raises `CandidateFormatError` with a detail the retry turn can quote back.
    """
    if not isinstance(payload, dict):
        not_object = "최상위 값이 JSON 객체가 아닙니다."
        raise CandidateFormatError(not_object)
    candidates = payload.get(CANDIDATES_KEY)
    if not isinstance(candidates, list):
        no_key = f"최상위 객체에 {CANDIDATES_KEY} 배열이 없습니다."
        raise CandidateFormatError(no_key)
    if not candidates or len(candidates) > expected:
        wrong_length = f"후보 {expected}개가 필요하지만 {len(candidates)}개를 받았습니다."
        raise CandidateFormatError(wrong_length)
    try:
        drafts = _DRAFTS.validate_python(candidates)
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
    happily return three candidates in its favourite one and call the set covered. A short
    answer is held to the first assignments rather than to all of them — the candidates
    that did arrive still have to be the ones that were asked for, in order.
    """
    written = tuple(draft.persona_domain for draft in drafts)
    assigned = domains[: len(written)]
    if written != assigned:
        expected = ", ".join(domain.value for domain in assigned)
        received = ", ".join("(없음)" if domain is None else domain.value for domain in written)
        mismatch = (
            f"persona_domain은 배정된 순서대로 [{expected}] 여야 하지만 [{received}] 를 받았습니다."
        )
        raise CandidateFormatError(mismatch)


def _detail(error: ValidationError) -> str:
    lines = [
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in error.errors()[:5]
    ]
    return "; ".join(lines)[:_MAX_DETAIL_CHARS]
