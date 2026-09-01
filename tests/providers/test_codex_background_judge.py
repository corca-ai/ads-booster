from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NoReturn

import pytest

from ads_booster.providers.codex_background_judge import CodexBackgroundJudge
from ads_booster.providers.codex_cli import CodexCliError
from ads_booster.search.image.contracts import BackgroundBrief, JudgeCandidate
from ads_booster.transport.http import HttpResponse

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

_POST_FORBIDDEN = "the background judge must not post data"
_THUMB_A = "https://thumbs.example/a.png"
_THUMB_B = "https://thumbs.example/b.png"
_IMAGE_A = "https://images.example/a-original.png"
_IMAGE_B = "https://images.example/b-original.png"


@dataclass
class _RecordingCodex:
    answer: JsonObject
    prompts: list[str] = field(default_factory=list)
    workspaces: list[list[str]] = field(default_factory=list)

    def run_generation_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        del schema, timeout_seconds
        self.prompts.append(prompt)
        self.workspaces.append(sorted(p.name for p in workspace.iterdir()))
        return self.answer


@dataclass(frozen=True, slots=True)
class _ThumbnailHttp:
    responses: dict[str, HttpResponse]

    def get(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        assert "image" in headers["Accept"]
        return self.responses[url]

    def post_json(self, url: str, payload: JsonObject, headers: Mapping[str, str]) -> NoReturn:
        del url, payload, headers
        raise AssertionError(_POST_FORBIDDEN)

    def post_form(self, url: str, form: Mapping[str, str], headers: Mapping[str, str]) -> NoReturn:
        del url, form, headers
        raise AssertionError(_POST_FORBIDDEN)


def _candidates() -> tuple[JudgeCandidate, ...]:
    return (
        JudgeCandidate(
            image_url=_IMAGE_A, thumbnail_url=_THUMB_A, title="포스터", width=1090, height=1902
        ),
        JudgeCandidate(
            image_url=_IMAGE_B, thumbnail_url=_THUMB_B, title="사진", width=773, height=1031
        ),
    )


def _brief() -> BackgroundBrief:
    return BackgroundBrief(
        query="KIA 타이거즈 배경화면", country="KR", persona="직장인, KIA 타이거즈"
    )


def _http_ok() -> _ThumbnailHttp:
    return _ThumbnailHttp(
        {
            _THUMB_A: HttpResponse(200, b"thumb-a", {}),
            _THUMB_B: HttpResponse(200, b"thumb-b", {}),
        }
    )


def test_judge_asks_once_about_the_whole_shortlist_and_maps_numbers_back_to_originals() -> None:
    # Given a judge that keeps only the second row
    codex = _RecordingCodex(answer={"keep": [2]})
    judge = CodexBackgroundJudge(codex=codex, http=_http_ok())

    # When the shortlist is judged
    accepted = judge.choose(_brief(), _candidates())

    # Then the answer names the original image, not the thumbnail it looked at
    assert accepted == (_IMAGE_B,)
    # And it was one call, with every thumbnail on disk for the model to open
    assert len(codex.prompts) == 1
    assert codex.workspaces == [["01.png", "02.png"]]
    # And the brief reached the prompt, so a row from somebody else's life is recognisable
    assert "KIA 타이거즈" in codex.prompts[0]
    assert "KR" in codex.prompts[0]


def test_judge_preserves_the_order_the_model_ranked() -> None:
    # Given the model puts the second row first
    codex = _RecordingCodex(answer={"keep": [2, 1]})
    judge = CodexBackgroundJudge(codex=codex, http=_http_ok())

    # When the shortlist is judged
    accepted = judge.choose(_brief(), _candidates())

    # Then best-first is carried through rather than re-sorted
    assert accepted == (_IMAGE_B, _IMAGE_A)


def test_judge_leaves_out_a_row_whose_thumbnail_could_not_be_fetched() -> None:
    # Given one thumbnail is unavailable
    codex = _RecordingCodex(answer={"keep": [2]})
    http = _ThumbnailHttp(
        {_THUMB_A: HttpResponse(404, b"", {}), _THUMB_B: HttpResponse(200, b"b", {})}
    )
    judge = CodexBackgroundJudge(codex=codex, http=http)

    # When the shortlist is judged
    accepted = judge.choose(_brief(), _candidates())

    # Then only the row that was actually looked at can be accepted, and the numbering does
    # not shift under the model: a row nobody saw has not been judged.
    assert accepted == (_IMAGE_B,)
    assert codex.workspaces == [["02.png"]]


def test_judge_returns_nothing_when_the_model_keeps_nothing() -> None:
    # Given the model finds nothing usable
    judge = CodexBackgroundJudge(codex=_RecordingCodex(answer={"keep": []}), http=_http_ok())

    # When the shortlist is judged
    # Then an empty verdict is a verdict; the fetcher turns it into a visible failure
    assert judge.choose(_brief(), _candidates()) == ()


def test_judge_rejects_an_answer_that_is_not_the_shape_it_asked_for() -> None:
    # Given the model answers with something other than a list of numbers
    judge = CodexBackgroundJudge(codex=_RecordingCodex(answer={"keep": "all"}), http=_http_ok())

    # When the shortlist is judged
    with pytest.raises(CodexCliError):
        _ = judge.choose(_brief(), _candidates())


def test_judge_does_not_call_the_model_for_an_empty_shortlist() -> None:
    # Given nothing survived the earlier gates
    codex = _RecordingCodex(answer={"keep": [1]})
    judge = CodexBackgroundJudge(codex=codex, http=_http_ok())

    # When the empty shortlist is judged
    accepted = judge.choose(_brief(), ())

    # Then no round trip is spent on it
    assert accepted == ()
    assert codex.prompts == []
