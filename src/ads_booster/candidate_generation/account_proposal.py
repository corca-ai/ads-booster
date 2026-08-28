"""Proposed marketing accounts, grounded in what the reference corpus already measured.

Opening an account meant writing twelve fields from a blank form — a name, an age, a
region, an occupation, a concept, a domain, three interests, a life rhythm, a background
subject and mood. That is an authoring job, and it was the reason accounts got created one
at a time and always looked alike.

The proposal turns it into a choice. What makes a choice worth offering is that it is
grounded: the reference index records, per post, which speaker cluster reached people and
which did not, so the proposal is asked to read that table and say which row it is standing
on. Nothing here is stored — a proposal that nobody picks should leave no trace, and the
one that is picked becomes an ordinary account through the ordinary route.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Final, Protocol

from pydantic import Field, TypeAdapter, ValidationError

from ads_booster.auth.codex import OAuthError
from ads_booster.candidate_generation.errors import (
    CandidateAuthRequiredError,
    CandidateContextMissingError,
    CandidateFormatError,
    CandidateProviderError,
)
from ads_booster.candidate_generation.models import GenerationModel
from ads_booster.candidate_generation.parsing import strip_json_fence
from ads_booster.providers.errors import ProviderError
from ads_booster.workspace import (
    CandidateBackgroundSubject,
    CandidatePersonaDomain,
    LockScreenFont,
    MarketingAccountIdentity,
)

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from pathlib import Path

    from ads_booster.agent.session import ModelClient
    from ads_booster.transport.json_types import JsonObject
    from ads_booster.workspace import MarketingAccountRecord


class CandidateModelSource(Protocol):
    """Opens one model client per call.

    Declared here rather than imported from `ports`, which names this module's own
    `AccountProposal`; stating the two-method duck locally keeps that one-way.
    """

    def open(self) -> AbstractContextManager[ModelClient]: ...


DEFAULT_PROPOSAL_COUNT: Final = 3
_INDEX_DOCUMENT: Final = "references/{country}/INDEX.md"
_MAX_INDEX_CHARS: Final = 20_000


class AccountProposal(GenerationModel):
    """One account the model suggests opening, with the evidence it stood on.

    `identity` is exactly the shape the create form submits, so a chosen proposal fills the
    form rather than travelling down a second path. `reason` is what the reviewer reads
    before choosing, and it has to name the reference ids it argues from — a suggestion
    that cannot say why is a suggestion nobody can check.
    """

    identity: MarketingAccountIdentity
    reason: Annotated[str, Field(min_length=1, max_length=400)]


_PROPOSALS: TypeAdapter[tuple[AccountProposal, ...]] = TypeAdapter(tuple[AccountProposal, ...])

_INSTRUCTION: Final = """당신은 Trace 마케팅 계정을 제안하는 에이전트입니다.
아래 레퍼런스 인덱스와 이미 운영 중인 계정 목록만 근거로, {country} 계정 후보 {count}개를
서로 다른 화자 유형으로 제안하세요.

[레퍼런스 인덱스]
이 표의 domain_cluster는 화자 유형이고, outcome과 relative는 그 유형이 실제로 도달한
결과입니다. 어떤 유형이 반응을 얻었는지 이 표에서 읽고, 그 근거를 reason에 적으세요.
{index}

[이미 운영 중인 계정]
{existing}
위 계정들과 도메인·직업·컨셉이 겹치지 않게 하세요. 같은 사람을 다시 제안하지 마세요.

[반드시 지킬 규칙]
1. 개발·메이커 소재를 제안하지 마세요. 인덱스에 1인개발·빌딩인퍼블릭 계열의 성과가
   있더라도 그것은 우리가 쓸 수 있는 유형이 아닙니다. 계정 필드에 배포·코딩·개발·출시·
   앱 제작 같은 말이 들어가면 그 계정이 쓰는 모든 글의 소재 통이 오염됩니다.
   직업이 개발자인 계정도 제안하지 마세요.
2. display_name은 실제로 있을 법한 한국 이름입니다. 별명·영문 아이디·수식어를 붙이지
   마세요.
3. age는 13~99의 구체적인 숫자, region은 "서울 성동구", "부산 해운대구"처럼 구·군까지
   적습니다.
4. occupation은 실제 직업명 하나입니다.
5. concept은 이 계정이 무엇을 쓰는 계정인지 한 문장으로 알아볼 수 있게 씁니다.
   장황한 설명이 아니라 사람이 읽고 바로 그림이 그려지는 한 줄입니다.
6. interests는 정확히 3개이고, 고유명사급으로 구체적이어야 합니다.
   "운동", "음악" 같은 범용어는 금지입니다.
7. life_rhythm은 시각이 드러나는 구체적인 하루입니다.
   예: "야간 근무 주간은 오후 4시 출근, 비번날 오전에 몰아서 잔다".
8. domain은 아래 토큰 중 하나입니다: {domains}
9. taste.background_subject는 아래 토큰 중 하나입니다: {subjects}
   taste.background_mood는 실제로 보이는 화면을 40자 안에서 구체적으로 씁니다.
   taste.font는 아래 토큰 중 하나입니다: {fonts}
10. reason은 이 유형이 통한다고 보는 이유와 근거 레퍼런스 id를 함께 적습니다.
   예: "kr-014·kr-003처럼 질문형 훅이 도달을 만든 사례가 있고, 이 계정의 하루는
   질문으로 열기 좋다". 200자 안에서 씁니다.

[출력 형식]
설명·머리말·코드펜스 없이 JSON 배열 하나만 출력하세요.
배열은 정확히 {count}개의 객체를 담고, 각 객체는 아래 키만 가집니다.

[
  {{
    "identity": {{
      "display_name": "이서진",
      "age": 27,
      "region": "서울 마포구",
      "occupation": "병동 간호사",
      "concept": "3교대를 잠금화면 일정으로 버티는 간호사",
      "domain": "office_worker",
      "interests": ["쿠로미", "필라테스", "동네 베이커리 투어"],
      "life_rhythm": "데이 근무일은 5시 40분 기상, 나이트 주간은 낮에 잔다",
      "taste": {{
        "background_subject": "character_other",
        "background_mood": "파스텔 톤 캐릭터가 크게 나온 화면",
        "font": "sf_pro_rounded"
      }}
    }},
    "reason": "근거 레퍼런스 id를 포함한 한두 문장"
  }}
]
위 예시는 형식 참고용입니다. 예시의 이름·직업·관심사를 그대로 쓰지 말고 새로 정하세요."""

_NO_EXISTING: Final = "아직 이 국가에 운영 중인 계정이 없습니다."
_EXISTING_LINE: Final = "- {display_name} ({occupation}, {domain}): {concept}"


@dataclass(frozen=True, slots=True)
class AccountProposalGenerator:
    """Asks the model for accounts worth opening, reading the reference index for evidence.

    The generator is deliberately thin: one call, one strict JSON answer, no retry loop and
    no store. A proposal that fails to parse is a failed suggestion, not a failed batch —
    the person presses the button again.
    """

    models: CandidateModelSource
    context_directory: Path
    model: str
    count: int = DEFAULT_PROPOSAL_COUNT

    def propose(
        self,
        country: str,
        existing: tuple[MarketingAccountRecord, ...] = (),
    ) -> tuple[AccountProposal, ...]:
        instruction = self.instruction(country, existing)
        with self.models.open() as client:
            answer = self._respond(client, instruction)
        return self._parse(answer)

    def instruction(
        self,
        country: str,
        existing: tuple[MarketingAccountRecord, ...] = (),
    ) -> str:
        """Assemble the one prompt this generator sends, so a test can read it."""
        return _INSTRUCTION.format(
            country=country,
            count=self.count,
            index=self._index(country),
            existing=_existing_section(country, existing),
            domains=", ".join(domain.value for domain in CandidatePersonaDomain),
            subjects=", ".join(subject.value for subject in CandidateBackgroundSubject),
            fonts=", ".join(font.value for font in LockScreenFont),
        )

    def _index(self, country: str) -> str:
        path = self.context_directory / _INDEX_DOCUMENT.format(country=country)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise CandidateContextMissingError(
                self.context_directory,
                (_INDEX_DOCUMENT.format(country=country),),
            ) from error
        return text[:_MAX_INDEX_CHARS]

    def _respond(self, client: ModelClient, instruction: str) -> str:
        history: tuple[JsonObject, ...] = ({"role": "user", "content": instruction},)
        try:
            return client.respond(history, ()).text
        except OAuthError as error:
            raise CandidateAuthRequiredError from error
        except ProviderError as error:
            raise CandidateProviderError(
                context_overflow=error.context_overflow,
                provider_code=error.code,
            ) from error

    def _parse(self, answer: str) -> tuple[AccountProposal, ...]:
        try:
            proposals = _PROPOSALS.validate_json(strip_json_fence(answer))
        except ValidationError as error:
            raise CandidateFormatError(str(error)[:_MAX_DETAIL]) from error
        if not proposals:
            raise CandidateFormatError(_EMPTY_DETAIL)
        return proposals[: self.count]


_MAX_DETAIL: Final = 500
_EMPTY_DETAIL: Final = "제안이 하나도 오지 않았습니다."


def _existing_section(country: str, existing: tuple[MarketingAccountRecord, ...]) -> str:
    lines = [
        _EXISTING_LINE.format(
            display_name=record.identity.display_name,
            occupation=record.identity.occupation,
            domain=record.identity.domain.value,
            concept=record.identity.concept,
        )
        for record in existing
        if record.country == country
    ]
    return "\n".join(lines) if lines else _NO_EXISTING
