"""Choosing the candidate background by judgement rather than by search rank.

The open-web fetcher can only prove an image downloads and decodes. This module runs the
editorial decision on top of it: collect several usable images, have the model look at all
of them, gate the ones that are obviously wrong, grade the survivors against the team's
authenticity rubric, and take the winner. A round that produces nothing worth using is
retried once with a rewritten query and then fails loudly — falling back to the first
search hit would hand the reviewer exactly the image the judge just rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

from ads_booster.candidate_generation.background_judge import (
    BackgroundJudge,
    JudgeError,
    JudgePersona,
)
from ads_booster.search.image.background import BackgroundSearchError, SearchedBackground
from ads_booster.workspace import (
    CandidateBackgroundAttempt,
    CandidateBackgroundGrade,
    CandidateBackgroundGrades,
    CandidateBackgroundJudgment,
    CandidateBackgroundReview,
    CandidateQuerySource,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ads_booster.candidate_generation.background_judge import JudgeVerdict, PairwiseOutcome
    from ads_booster.search.image.open_background import (
        CollectedBackground,
        CollectedBackgrounds,
        OpenWebBackgroundFetcher,
    )
    from ads_booster.transport.json_types import JsonValue

_GRADE_POINTS: Final = {
    CandidateBackgroundGrade.HIGH: 3,
    CandidateBackgroundGrade.MID: 2,
    CandidateBackgroundGrade.LOW: 1,
}
_TIE_MARGIN: Final = 1
_MAX_QUERIES: Final = 3
_MIN_TOKENS_TO_BROADEN: Final = 3
_WIDE_QUERY_TOKENS: Final = 4
_MAX_REASON_CHARS: Final = 500
_MAX_COLLECTED: Final = 6
_JUDGMENT: TypeAdapter[CandidateBackgroundJudgment] = TypeAdapter(CandidateBackgroundJudgment)
JUDGE_REJECTED_CODE: Final = "background_judge_rejected"
JUDGE_REJECTED_MESSAGE: Final = "background judge accepted none of the collected images"
JUDGE_FAILED_CODE: Final = "background_judge_failed"
JUDGE_FAILED_MESSAGE: Final = "background judge did not return a usable verdict"
EXHAUSTED_CODE: Final = "background_search_exhausted"
_WRITE_FAILED_CODE: Final = "background_artifact_write_failed"
_WRITE_FAILED_MESSAGE: Final = "judged background could not be written"


@dataclass(frozen=True, slots=True)
class JudgedBackground:
    """The background that won, together with the judgement that chose it."""

    background: SearchedBackground
    judgment: CandidateBackgroundJudgment


@dataclass(frozen=True, slots=True)
class _Round:
    """One collection-and-judging pass, kept whole so its reviews can be recorded."""

    collected: CollectedBackgrounds
    verdicts: tuple[JudgeVerdict, ...]
    survivors: tuple[tuple[CollectedBackground, int], ...]
    failures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JudgedBackgroundSelector:
    """Collects candidate backgrounds and picks the one the judge says the persona would keep.

    `model` is recorded on the judgement rather than used to make the call: the client the
    judge holds is already bound to a model, and the reviewer needs to see which one spoke.
    """

    fetcher: OpenWebBackgroundFetcher
    judge: BackgroundJudge
    model: str
    limit: int = _MAX_COLLECTED

    def select(self, persona: JudgePersona, destination: Path) -> JudgedBackground:
        """Walk the query ladder until one query produces a background worth using.

        A query the model wrote from a persona's identity can name a person or a character
        that simply has no usable photo on the open web, and that is not the same failure
        as a photo the judge rejected. Both are retried here, and both spend from the same
        budget of `_MAX_QUERIES`, so two stages retrying cannot compound into a long stall
        in front of a reviewer who is waiting on an image.
        """
        attempts: list[CandidateBackgroundAttempt] = []
        query = persona.query
        source = CandidateQuerySource.ORIGINAL
        while True:
            collected = self.fetcher.collect(query, self.limit)
            attempts.append(
                CandidateBackgroundAttempt(
                    query=query,
                    source=source,
                    results=collected.results_seen,
                    passed_filters=collected.passed_filters,
                    filtered_stock=collected.filtered_stock,
                )
            )
            failures: tuple[str, ...] = ()
            if collected.images:
                round_ = self._grade(collected, _with_query(persona, query))
                if round_.survivors:
                    return self._decide(persona, round_, destination, attempts=tuple(attempts))
                failures = _judged_out(collected, round_.failures)
            if len(attempts) >= _MAX_QUERIES:
                raise BackgroundSearchError(EXHAUSTED_CODE, _diagnosis(attempts))
            query, source = self._next_query(query, source, collected, failures)

    def _next_query(
        self,
        query: str,
        source: CandidateQuerySource,
        collected: CollectedBackgrounds,
        failures: tuple[str, ...],
    ) -> tuple[str, CandidateQuerySource]:
        """Choose the next rung: widen the query for free before paying for a rewrite.

        A search that returned nothing is usually over-qualified, and dropping the trailing
        qualifiers is a fix that costs no provider call. Anything else — results that were
        unusable, or images the judge threw out — needs the model to say something new.
        """
        if source is CandidateQuerySource.ORIGINAL and not collected.images:
            broadened = _broaden(query)
            if broadened is not None:
                return broadened, CandidateQuerySource.BROADENED
        return (
            self._rewrite(query, failures or (_collection_failure(collected),)),
            CandidateQuerySource.REWRITTEN,
        )

    def _grade(self, collected: CollectedBackgrounds, persona: JudgePersona) -> _Round:
        """Judge one collected pool and split it into survivors and the reasons for the rest."""
        try:
            verdicts = self.judge.grade(persona, collected.images)
        except JudgeError as error:
            raise BackgroundSearchError(JUDGE_FAILED_CODE, JUDGE_FAILED_MESSAGE) from error
        by_id = {image.image_id: image for image in collected.images}
        survivors: list[tuple[CollectedBackground, int]] = []
        failures: list[str] = []
        for verdict in verdicts:
            image = by_id.get(verdict.id)
            if image is None:
                continue
            grades = verdict.grades
            # An image the judge could not vouch for on authenticity is not a near miss: the
            # whole point of the rubric is that a staged promo shot never reaches a reviewer.
            if verdict.gated or grades is None:
                failures.append(verdict.gate_reason or verdict.note or "게이트 탈락")
                continue
            if grades.authenticity is CandidateBackgroundGrade.LOW:
                failures.append(verdict.note or "진정성 하")
                continue
            survivors.append((image, _score(grades)))
        return _Round(
            collected=collected,
            verdicts=verdicts,
            survivors=tuple(survivors),
            failures=tuple(failures),
        )

    def _rewrite(self, query: str, failures: tuple[str, ...]) -> str:
        try:
            return self.judge.rewrite_query(query, failures)
        except JudgeError as error:
            raise BackgroundSearchError(JUDGE_FAILED_CODE, JUDGE_FAILED_MESSAGE) from error

    def _decide(
        self,
        persona: JudgePersona,
        round_: _Round,
        destination: Path,
        *,
        attempts: tuple[CandidateBackgroundAttempt, ...],
    ) -> JudgedBackground:
        ranked = sorted(round_.survivors, key=lambda entry: entry[1], reverse=True)
        winner, top = ranked[0]
        reason = _reason_for(round_.verdicts, winner.image_id)
        tie_broken = False
        inconsistent = False
        if len(ranked) > 1 and top - ranked[1][1] <= _TIE_MARGIN:
            outcome = self._break_tie(persona, ranked[0][0], ranked[1][0])
            if outcome.winner is None:
                # Both orders were asked and they disagreed. The graded totals are the only
                # thing left that did not come from a coin flip, so they decide.
                inconsistent = True
                reason = f"{outcome.reason} ({reason})"[:_MAX_REASON_CHARS]
            else:
                winner = outcome.winner
                reason = outcome.reason
                tie_broken = True
        return JudgedBackground(
            background=_write(round_, winner, destination),
            judgment=CandidateBackgroundJudgment(
                reviews=_reviews(round_),
                chosen_id=winner.image_id,
                reason=reason,
                model=self.model,
                query=persona.query,
                rewritten_query=_last_rewritten(attempts),
                attempts=attempts,
                tie_broken=tie_broken,
                tie_break_inconsistent=inconsistent,
            ),
        )

    def _break_tie(
        self,
        persona: JudgePersona,
        left: CollectedBackground,
        right: CollectedBackground,
    ) -> PairwiseOutcome:
        try:
            return self.judge.compare(persona, left, right)
        except JudgeError as error:
            raise BackgroundSearchError(JUDGE_FAILED_CODE, JUDGE_FAILED_MESSAGE) from error


@dataclass(frozen=True, slots=True)
class JudgedBackgroundFetcher:
    """The judged selector, shaped as the `BackgroundFetcher` the Trace runner already calls.

    `GenerateOneRunner` asks for one background by query and writes the artifact plus its
    provenance file; nothing about that contract changes here. What changes is who decides:
    instead of the first stock hit that downloads, the persona's own judge picks from a
    collected pool, and the judgment travels with the artifact so the downstream wallpaper
    set, native capture, and provenance validation see the same file they always did.
    """

    selector: JudgedBackgroundSelector
    persona: JudgePersona

    def fetch(self, query: str, destination: Path) -> SearchedBackground:
        judged = self.selector.select(_with_query(self.persona, query), destination)
        return replace(judged.background, details=_judgment_details(judged.judgment))


def _judgment_details(judgment: CandidateBackgroundJudgment) -> dict[str, JsonValue]:
    """Carry the whole judgment onto the artifact record.

    The full record travels rather than a summary because the artifact file is where the
    native path reads the judgment back from: the fetcher runs inside the Trace runner and
    has no route to the candidate store, so the file it already writes is the only handoff.
    """
    return {"selection": "ai_judged", "judgment": _JUDGMENT.dump_python(judgment, mode="json")}


def _write(
    round_: _Round,
    winner: CollectedBackground,
    destination: Path,
) -> SearchedBackground:
    """Write the winning bytes only, so the artifact on disk is the image that was judged."""
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(winner.content)
    except OSError as error:
        raise BackgroundSearchError(_WRITE_FAILED_CODE, _WRITE_FAILED_MESSAGE) from error
    return SearchedBackground(
        path=destination,
        sha256=sha256(winner.content).hexdigest(),
        query=round_.collected.query,
        provider=round_.collected.provider,
        image_url=winner.image_url,
        source_url=winner.source_url,
    )


def _broaden(query: str) -> str | None:
    """Drop the trailing qualifiers, which is where an over-specific query usually fails.

    Returns `None` when the query is already short enough that widening it would throw
    away the subject itself rather than the qualifiers around it.
    """
    tokens = query.split()
    if len(tokens) < _MIN_TOKENS_TO_BROADEN:
        return None
    dropped = 2 if len(tokens) >= _WIDE_QUERY_TOKENS else 1
    return " ".join(tokens[:-dropped])


def _judged_out(collected: CollectedBackgrounds, reasons: Sequence[str]) -> tuple[str, ...]:
    """Summarise a whole-pool rejection so the rewrite is told what went wrong, not just that.

    A bare list of per-image notes reads as noise to the rewriting model. Leading with the
    count makes the pattern the point: every image this query found was unusable, which is
    a fact about the query rather than about any one image.
    """
    distinct: list[str] = []
    for reason in reasons:
        if reason not in distinct:
            distinct.append(reason)
    summary = f"직전 검색어의 이미지 {len(collected.images)}장이 전부 배경 심사에서 탈락"
    if collected.filtered_stock:
        summary = f"{summary} (스톡 사이트 결과 {collected.filtered_stock}건은 수집 전에 제외)"
    return (summary, *distinct)


def _collection_failure(collected: CollectedBackgrounds) -> str:
    """Tell the rewrite prompt which kind of empty pool it is being asked to fix."""
    if collected.results_seen == 0:
        return "검색 결과 0건"
    if collected.filtered_stock == collected.results_seen:
        return f"결과 {collected.results_seen}건이 모두 스톡 사진 사이트"
    return f"결과 {collected.results_seen}건이 모두 크기·형식 검증에서 탈락"


def _diagnosis(attempts: Sequence[CandidateBackgroundAttempt]) -> str:
    """Say which of the three failures happened, and name every query that was tried."""
    results = sum(attempt.results for attempt in attempts)
    passed = sum(attempt.passed_filters for attempt in attempts)
    if results == 0:
        detail = "검색 결과가 없었습니다"
    elif passed == 0:
        detail = f"결과 {results}건이 모두 검증(크기·형식·중복)에서 탈락했습니다"
    else:
        detail = f"찾은 이미지 {passed}장이 모두 배경 심사에서 탈락했습니다"
    tried = " · ".join(f"“{attempt.query}”" for attempt in attempts)
    return (
        f"적합한 배경을 찾지 못했습니다 — {detail}. "
        f"시도한 검색어: {tried} — 검색어를 조정해 다시 시도해 주세요."
    )


def _last_rewritten(attempts: Sequence[CandidateBackgroundAttempt]) -> str | None:
    rewritten = [
        attempt.query for attempt in attempts if attempt.source is not CandidateQuerySource.ORIGINAL
    ]
    return rewritten[-1] if rewritten else None


def _score(grades: CandidateBackgroundGrades) -> int:
    return (
        _GRADE_POINTS[grades.authenticity]
        + _GRADE_POINTS[grades.persona_fit]
        + _GRADE_POINTS[grades.background_fit]
    )


def _reason_for(verdicts: tuple[JudgeVerdict, ...], image_id: str) -> str:
    for verdict in verdicts:
        if verdict.id == image_id and verdict.note:
            return verdict.note
    return "심사 기준 총점이 가장 높은 배경입니다."


def _with_query(persona: JudgePersona, query: str) -> JudgePersona:
    return JudgePersona(
        topic=persona.topic,
        subject=persona.subject,
        mood=persona.mood,
        query=query,
    )


def _reviews(round_: _Round) -> tuple[CandidateBackgroundReview, ...]:
    by_id = {verdict.id: verdict for verdict in round_.verdicts}
    return tuple(_review(image, by_id.get(image.image_id)) for image in round_.collected.images)


def _review(
    image: CollectedBackground,
    verdict: JudgeVerdict | None,
) -> CandidateBackgroundReview:
    if verdict is None:
        return CandidateBackgroundReview(
            image_id=image.image_id,
            image_url=image.image_url,
            source_url=image.source_url,
            gated=True,
            gate_reason="심사 결과가 돌아오지 않았습니다",
        )
    grades = None if verdict.gated else verdict.grades
    return CandidateBackgroundReview(
        image_id=image.image_id,
        image_url=image.image_url,
        source_url=image.source_url,
        gated=verdict.gated,
        gate_reason=verdict.gate_reason if verdict.gated else None,
        grades=grades,
        score=None if grades is None else _score(grades),
        note=verdict.note,
    )
