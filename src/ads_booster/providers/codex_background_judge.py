"""A background judge that looks at the shortlist through the Codex CLI.

The seam it fills is `BackgroundJudge`. What it adds over the geometry ranking is sight:
whether an image has words burned into it, and whether its subject belongs to this
persona. Both are invisible in a row's host and title - measured on a live pool, a KIA
championship poster and a cricket photograph won on geometry alone with clean metadata.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

import httpx2

from ads_booster.providers.codex_cli import CodexCliError

if TYPE_CHECKING:
    from ads_booster.providers.codex_cli import CodexCli
    from ads_booster.search.image.contracts import BackgroundBrief, JudgeCandidate
    from ads_booster.transport.http import HttpClient
    from ads_booster.transport.json_types import JsonObject

_HTTP_OK: Final = 200
_DEFAULT_TIMEOUT_SECONDS: Final = 120.0
# Thumbnails, not originals. The two things being judged - burnt-in text and a subject that
# belongs to somebody else's life - are both legible small, so sending originals multiplies
# the payload for no extra signal.
_THUMBNAIL_HEADERS: Final = {
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "User-Agent": "trace-agent/0.2.1",
}
_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "keep": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1},
            "description": "Numbers of the usable images, best first. Empty if none is usable.",
        }
    },
    "required": ["keep"],
}
_PROMPT: Final = """당신은 잠금화면 배경으로 쓸 사진을 고릅니다.

이 잠금화면의 주인:
- 나라: {country}
- 이 사람에 대해: {persona}
- 이 배경을 찾으려고 검색한 말: {query}

작업 폴더에 후보 이미지가 번호대로 들어 있습니다. 하나씩 열어서 **보고** 판단하세요.

떨어뜨릴 것:
- 사진 안에 글자·문구·로고·워터마크가 박힌 것. 이 사진 위에 일정 텍스트를 다시 얹기
  때문에 글자가 겹칩니다. 포스터, 홍보물, 짤, 기사 캡처가 여기 해당합니다.
- 이 사람과 무관한 대상. 다른 나라 사람이나 이 사람이 좋아하지도 않는 팀·인물이
  찍힌 사진은 검색어가 비슷해도 이 사람 폰에 있을 리 없습니다.
- 모르는 개인의 얼굴이 알아볼 만하게 나온 것. 공인은 괜찮습니다.

남길 것:
- 실제로 누군가 자기 폰 배경으로 저장했을 법한 사진이나 그림.
- 위에 글자를 얹어도 읽힐 만큼 조용한 것이면 더 좋습니다.

{listing}

쓸 수 있는 것의 번호만 좋은 순서대로 keep에 담아 주세요. 하나도 없으면 빈 배열입니다.
애매하면 떨어뜨리세요. 배경이 없으면 사람이 확인하지만, 잘못된 배경은 그냥 나갑니다.
"""


@dataclass(frozen=True, slots=True)
class CodexBackgroundJudge:
    codex: CodexCli
    http: HttpClient
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS

    def choose(
        self,
        brief: BackgroundBrief,
        candidates: tuple[JudgeCandidate, ...],
    ) -> tuple[str, ...]:
        """Ask once, about the whole shortlist, and map the answer back to urls.

        The reply names numbers rather than urls: a url is long enough that a model
        rewriting one by a character would silently drop a usable row.
        """
        if not candidates:
            return ()
        with tempfile.TemporaryDirectory(prefix="trace-background-judge-") as directory:
            workspace = Path(directory)
            numbered = self._download(workspace, candidates)
            if not numbered:
                return ()
            listing = "\n".join(
                f"{number}. {path.name} — 검색 결과 제목: {candidate.title[:80]}"
                for number, (path, candidate) in sorted(numbered.items())
            )
            prompt = _PROMPT.format(
                country=brief.country or "알 수 없음",
                persona=brief.persona or "알려진 것 없음",
                query=brief.query,
                listing=listing,
            )
            answer = self.codex.run_generation_job(
                prompt,
                _SCHEMA,
                workspace=workspace,
                timeout_seconds=self.timeout_seconds,
            )
        return _accepted(answer, numbered)

    def _download(
        self,
        workspace: Path,
        candidates: tuple[JudgeCandidate, ...],
    ) -> dict[int, tuple[Path, JudgeCandidate]]:
        """Put each thumbnail in the workspace under its number.

        A thumbnail that cannot be fetched is left out rather than passed through unseen:
        the judge exists because rows that look fine in metadata are not, so a row nobody
        looked at has not been judged.
        """
        numbered: dict[int, tuple[Path, JudgeCandidate]] = {}
        for index, candidate in enumerate(candidates, start=1):
            try:
                response = self.http.get(candidate.thumbnail_url, _THUMBNAIL_HEADERS)
            except httpx2.HTTPError:
                continue
            if response.status_code != _HTTP_OK:
                continue
            path = workspace / f"{index:02d}.png"
            try:
                _ = path.write_bytes(response.content)
            except OSError:
                continue
            numbered[index] = (path, candidate)
        return numbered


def _accepted(
    answer: JsonObject,
    numbered: dict[int, tuple[Path, JudgeCandidate]],
) -> tuple[str, ...]:
    keep = answer.get("keep")
    if not isinstance(keep, list):
        message = "codex_background_judge_answer_invalid"
        raise CodexCliError(message)
    urls: list[str] = []
    for value in keep:
        if not isinstance(value, int) or isinstance(value, bool):
            continue
        entry = numbered.get(value)
        if entry is not None:
            urls.append(entry[1].image_url)
    return tuple(dict.fromkeys(urls))
