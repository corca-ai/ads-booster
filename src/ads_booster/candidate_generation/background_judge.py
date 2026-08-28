"""AI judging of the candidate background images, with the images actually attached.

Taking the first search hit that downloads is a physical check, not an editorial one: it
cannot tell a stock-watermarked promo shot from the kind of photo a real person keeps on
their phone. This module asks the model to look at every collected image and say so, in
one call carrying all the previews as `input_image` content parts. A judge that never saw
the pixels would be a fake judge, so the images travel with the prompt or the call fails.

The model answers strict JSON. Parsing is fence-tolerant and retried once with the
validation detail quoted back, the same contract the caption generation call uses.
"""

from __future__ import annotations

import base64
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final, override

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ads_booster.candidate_generation.parsing import strip_json_fence
from ads_booster.workspace import CandidateBackgroundGrades  # noqa: TC001 — pydantic field

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ads_booster.agent.session import ModelClient
    from ads_booster.search.image.open_background import CollectedBackground
    from ads_booster.transport.json_types import JsonObject, JsonValue

_MAX_DETAIL_CHARS: Final = 500
_MAX_QUERY_CHARS: Final = 200
JUDGE_FORMAT_CODE: Final = "background_judge_format"
JUDGE_FORMAT_MESSAGE: Final = "background judge did not answer with the required JSON"

_GATE_RULES: Final = (
    "1) 워터마크나 스톡 사진 오버레이가 보인다",
    "2) 이미지 안에 글자가 크게 박혀 있다",
    "3) 자막이 달린 뉴스·방송 화면 캡처다",
    "4) 굿즈 사진이거나 여러 장을 이어붙인 썸네일 콜라주다",
    "5) 검색어가 가리키는 대상을 찍은 사진이 아니다",
)
# The rubric follows G-Eval's shape: each criterion states its scale and definition, then the
# numbered steps the judge should walk before it commits to a grade. The explicit procedure is
# what keeps the 상/중 boundary from drifting between images in one batch.
_RUBRIC: Final = (
    (
        "① 진정성 (상/중/하) — 실제 유저가 자기 폰에 직접 저장했을 법한 사진인가.\n"
        "   1. 조명과 구도가 연출된 촬영물인지, 우연히 찍힌 장면인지 본다.\n"
        "   2. 광고·홍보·프로필용으로 제작된 티가 나는지 확인한다.\n"
        "   3. 연출 티가 뚜렷하면 하, 애매하면 중, 개인 소장 사진처럼 보이면 상."
    ),
    (
        "② 페르소나 적합 (상/중/하) — 아래 페르소나의 정체성·주제·분위기에 맞는가.\n"
        "   1. 페르소나 블록의 주제와 배경 분위기를 읽는다.\n"
        "   2. 사진의 소재·색감·계절감을 그 분위기와 대조한다.\n"
        "   3. 어긋나면 하, 무난하면 중, 이 페르소나가 고를 만하면 상."
    ),
    (
        "③ 배경 적성 (상/중/하) — 세로 잠금화면 배경으로 쓸 만한 구도인가.\n"
        "   1. 세로 화면에 담았을 때 핵심 피사체가 잘리는지 본다.\n"
        "   2. 위젯이 올라가는 상단·중앙부가 차분한지 확인한다.\n"
        "   3. 그 자리가 복잡하면 하, 보통이면 중, 비어 있고 차분하면 상."
    ),
)
_BIAS_RULES: Final = (
    "제시된 순서(첫 번째/마지막)는 품질과 무관합니다. 순서가 판단에 영향을 주지 않게 하세요.",
    "해상도·파일 크기·선명도는 품질 판단 근거가 아닙니다. 또렷하다고 진정성이 높은 것이 아닙니다.",
    "img-a 같은 식별자는 임의로 붙인 이름입니다. 이름 자체를 선호 근거로 삼지 마세요.",
)
_TASK_HEADER: Final = "당신은 페르소나의 잠금화면 배경 사진을 고르는 심사위원입니다."
_GATE_HEADER: Final = (
    "먼저 하드 게이트입니다. 하나라도 해당하면 탈락(gated=true)이고 등급은 매기지 않습니다."
)
_GRADE_HEADER: Final = "게이트를 통과한 사진만 아래 세 기준으로 평가합니다."
_BIAS_HEADER: Final = "판단에서 배제할 것:"
_SHAPE_HEADER: Final = (
    "출력은 설명 없이 JSON 배열 하나뿐입니다. 받은 사진마다 정확히 한 항목이고, "
    "각 항목은 근거(note)를 먼저 쓴 뒤 판정(gated 또는 grades)을 적습니다."
)
_SHAPE: Final = (
    '[{"note": "한 줄 근거", "id": "img-a", "gated": false, "grades": {"authenticity": "상", '
    '"persona_fit": "중", "background_fit": "상"}}, {"note": "한 줄 근거", "id": "img-b", '
    '"gated": true, "gate_reason": "워터마크"}]'
)
# The tie-break follows FastChat's pair-v2 contract: free-form comparison first, then one
# literal token at the end. Substring matching on the token survives whatever preamble the
# model decides to write, which a JSON schema does not.
_PAIRWISE_HEADER: Final = "이 페르소나가 실제로 자기 폰에 저장했을 사진은 A와 B 중 어느 쪽입니까?"
_PAIRWISE_FORMAT: Final = (
    "먼저 두 사진을 비교하는 근거를 두세 문장으로 쓰고, 마지막 줄에 반드시 다음 형식 중 "
    "하나만 그대로 출력하세요: A가 낫다면 [[A]], B가 낫다면 [[B]], 우열을 가릴 수 없으면 [[C]]."
)
_PAIRWISE_TOKENS: Final = {"[[A]]": "A", "[[B]]": "B", "[[C]]": "C"}
_INCONSISTENT_REASON: Final = "순서를 바꿔 물었을 때 판정이 뒤집혀 총점이 높은 쪽을 씁니다."
# The failure this fixes is always the same one: the query described the persona's job or
# situation, so the search returned staged still-lifes nobody would set as a wallpaper.
_REWRITE_TARGET: Final = (
    "실제 유저가 자기 폰 배경화면으로 저장했을 법한 사진이 나오는 검색어로 다시 써 주세요. "
    "직업 소품·작업 공간·물건 나열형 검색어는 스톡 사진만 부르니 피하고, "
    "그 사람의 취향(풍경·반려동물·캐릭터·좋아하는 인물)을 겨냥하세요."
)


@dataclass(frozen=True, slots=True)
class JudgeError(Exception):
    """The judge call came back in a shape the stage cannot use."""

    code: str
    message: str

    @override
    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class JudgePersona:
    """What the judge is told about the candidate whose background this is."""

    topic: str
    subject: str
    mood: str
    query: str


class JudgeVerdict(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=32)
    gated: bool
    gate_reason: str | None = Field(default=None, max_length=500)
    grades: CandidateBackgroundGrades | None = None
    note: str = Field(default="", max_length=500)


@dataclass(frozen=True, slots=True)
class _PairwiseAnswer:
    """One order of one pairwise comparison: the token it ended on and why."""

    choice: str
    reason: str


@dataclass(frozen=True, slots=True)
class PairwiseOutcome:
    """The result of asking the same pair in both orders.

    `winner` is `None` when the two orders disagreed or the judge called it even. That is
    not an error: it means the comparison did not earn the right to override the grades,
    and the caller keeps the higher-scoring image instead of trusting a coin flip.
    """

    winner: CollectedBackground | None
    reason: str
    consistent: bool


_VERDICTS: TypeAdapter[tuple[JudgeVerdict, ...]] = TypeAdapter(tuple[JudgeVerdict, ...])


def default_shuffle(
    images: Sequence[CollectedBackground],
) -> tuple[CollectedBackground, ...]:
    """Present the images in a random order so position cannot stand in for quality."""
    shuffled = list(images)
    random.shuffle(shuffled)
    return tuple(shuffled)


@dataclass(frozen=True, slots=True)
class BackgroundJudge:
    """Runs the grading, tie-break, and query-rewrite calls against one model client."""

    client: ModelClient
    # Injected in tests so a fixed presentation order can be asserted; production shuffles.
    shuffle: Callable[[Sequence[CollectedBackground]], Sequence[CollectedBackground]] = (
        default_shuffle
    )

    def grade(
        self,
        persona: JudgePersona,
        images: Sequence[CollectedBackground],
    ) -> tuple[JudgeVerdict, ...]:
        """Gate and grade every collected image in one call that carries all the previews."""
        presented = self._present(images)
        history: tuple[JsonObject, ...] = (
            {"role": "user", "content": _grading_content(persona, presented)},
        )
        return self._ask(history, _parse_verdicts)

    def compare(
        self,
        persona: JudgePersona,
        left: CollectedBackground,
        right: CollectedBackground,
    ) -> PairwiseOutcome:
        """Ask the same pair twice with the positions swapped, and require one answer.

        A single pairwise call measures position as much as it measures the images, so the
        pair is judged as (left, right) and again as (right, left). Only a verdict that
        names the same image both times is allowed to override the graded totals; a
        disagreement is reported as such rather than resolved by whichever order ran last.
        """
        first = self._one_comparison(persona, left, right)
        second = self._one_comparison(persona, right, left)
        first_winner = _positioned(first.choice, left, right)
        second_winner = _positioned(second.choice, right, left)
        if first_winner is not None and first_winner is second_winner:
            return PairwiseOutcome(
                winner=first_winner,
                reason=first.reason or "두 순서 모두 같은 사진을 골랐습니다.",
                consistent=True,
            )
        return PairwiseOutcome(winner=None, reason=_INCONSISTENT_REASON, consistent=False)

    def _one_comparison(
        self,
        persona: JudgePersona,
        first: CollectedBackground,
        second: CollectedBackground,
    ) -> _PairwiseAnswer:
        history: tuple[JsonObject, ...] = (
            {"role": "user", "content": _pairwise_content(persona, (first, second))},
        )
        return self._ask(history, _parse_pairwise)

    def rewrite_query(self, query: str, failures: Sequence[str]) -> str:
        """Ask for one better search query after the first collection judged out entirely."""
        observed = "; ".join(failures) if failures else "쓸 만한 사진이 하나도 없었습니다"
        prompt = "\n".join(
            (
                "다음 이미지 검색어로는 페르소나에게 어울리는 사진을 찾지 못했습니다.",
                f"검색어: {query}",
                f"관찰된 실패 양상: {observed}",
                "",
                _REWRITE_TARGET,
                "설명 없이 검색어 한 줄만 출력합니다.",
            )
        )
        history: tuple[JsonObject, ...] = ({"role": "user", "content": prompt},)
        answer = self.client.respond(history, ()).text.strip()
        rewritten = strip_json_fence(answer).strip().strip('"').strip()
        if not rewritten:
            raise JudgeError(JUDGE_FORMAT_CODE, "background judge returned an empty query")
        return rewritten[:_MAX_QUERY_CHARS]

    def _present(
        self,
        images: Sequence[CollectedBackground],
    ) -> tuple[CollectedBackground, ...]:
        return tuple(self.shuffle(images))

    def _ask[T](
        self,
        history: tuple[JsonObject, ...],
        parse: Callable[[str], T],
    ) -> T:
        """Ask once, and on a malformed answer quote the detail back for exactly one retry."""
        answer = self.client.respond(history, ()).text
        try:
            return parse(answer)
        except JudgeError as first_failure:
            retry: tuple[JsonObject, ...] = (
                *history,
                {"role": "assistant", "content": answer},
                {
                    "role": "user",
                    "content": (
                        f"직전 응답을 읽을 수 없었습니다: {first_failure.message}\n"
                        "설명이나 코드펜스 없이, 요청한 JSON만 다시 출력해 주세요."
                    ),
                },
            )
            return parse(self.client.respond(retry, ()).text)


def _grading_content(
    persona: JudgePersona,
    images: Sequence[CollectedBackground],
) -> list[JsonValue]:
    ids = ", ".join(image.image_id for image in images)
    sections = (
        f"{_TASK_HEADER} 아래 {len(images)}장({ids})을 각각 보고 판단해 주세요.",
        _GATE_HEADER + "\n" + "\n".join(_GATE_RULES),
        _GRADE_HEADER + "\n\n" + "\n\n".join(_RUBRIC),
        _BIAS_HEADER + "\n" + "\n".join(_BIAS_RULES),
        _persona_block(persona),
        _SHAPE_HEADER + "\n" + _SHAPE,
    )
    content: list[JsonValue] = [{"type": "input_text", "text": "\n\n".join(sections)}]
    for image in images:
        content.append({"type": "input_text", "text": f"[{image.image_id}]"})
        content.append({"type": "input_image", "image_url": _data_url(image)})
    return content


def _pairwise_content(
    persona: JudgePersona,
    images: Sequence[CollectedBackground],
) -> list[JsonValue]:
    """Present the pair as A and B by position, so the swapped call is a real swap.

    The image ids never appear here. If the judge saw them it could recognise the same
    pair across the two orders and simply repeat itself, which would turn the consistency
    check into a formality.
    """
    sections = (
        _TASK_HEADER,
        f"두 사진이 거의 같은 점수를 받았습니다. {_PAIRWISE_HEADER}",
        _BIAS_HEADER + "\n" + "\n".join(_BIAS_RULES),
        _persona_block(persona),
        _PAIRWISE_FORMAT,
    )
    content: list[JsonValue] = [{"type": "input_text", "text": "\n\n".join(sections)}]
    for label, image in zip(("A", "B"), images, strict=True):
        content.append({"type": "input_text", "text": f"[{label}]"})
        content.append({"type": "input_image", "image_url": _data_url(image)})
    return content


def _positioned(
    choice: str,
    first: CollectedBackground,
    second: CollectedBackground,
) -> CollectedBackground | None:
    """Map one call's positional verdict back onto the image that actually held it."""
    if choice == "A":
        return first
    if choice == "B":
        return second
    return None


def _persona_block(persona: JudgePersona) -> str:
    return (
        "페르소나\n"
        f"- 주제: {persona.topic}\n"
        f"- 배경 소재: {persona.subject}\n"
        f"- 배경 분위기: {persona.mood}\n"
        f"- 검색어: {persona.query}"
    )


def _data_url(image: CollectedBackground) -> str:
    encoded = base64.b64encode(image.preview).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _parse_verdicts(answer: str) -> tuple[JudgeVerdict, ...]:
    payload = strip_json_fence(answer)
    if not payload:
        raise JudgeError(JUDGE_FORMAT_CODE, "응답이 비어 있습니다.")
    try:
        return _VERDICTS.validate_json(payload)
    except ValidationError as error:
        raise JudgeError(JUDGE_FORMAT_CODE, _detail(error)) from error


def _parse_pairwise(answer: str) -> _PairwiseAnswer:
    """Take the verdict from the literal token, wherever in the answer it ended up.

    Substring matching beats a regex here for the same reason FastChat uses it: the model
    is asked to reason first, and the token has to survive whatever it wrote around it.
    """
    found = [token for token in _PAIRWISE_TOKENS if token in answer]
    if len(found) != 1:
        detail = "판정 토큰 [[A]] · [[B]] · [[C]] 중 정확히 하나가 있어야 합니다."
        raise JudgeError(JUDGE_FORMAT_CODE, detail)
    token = found[0]
    reason = answer.replace(token, " ").strip()[:_MAX_DETAIL_CHARS]
    return _PairwiseAnswer(choice=_PAIRWISE_TOKENS[token], reason=reason)


def _detail(error: ValidationError) -> str:
    lines = [
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in error.errors()[:5]
    ]
    return "; ".join(lines)[:_MAX_DETAIL_CHARS]
