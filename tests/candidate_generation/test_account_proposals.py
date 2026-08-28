from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from ads_booster.auth.codex import OAuthError
from ads_booster.candidate_generation import AccountProposalGenerator
from ads_booster.candidate_generation.errors import (
    CandidateAuthRequiredError,
    CandidateContextMissingError,
    CandidateFormatError,
    CandidateProviderError,
)
from ads_booster.providers.codex import ModelTurn
from ads_booster.providers.errors import ProviderError
from ads_booster.workspace import (
    CandidateBackgroundSubject,
    CandidatePersonaDomain,
    LockScreenFont,
    MarketingAccountCreate,
    MarketingAccountIdentity,
    MarketingAccountSchedule,
    MarketingAccountTaste,
    SqliteWorkspaceStore,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from ads_booster.agent.session import ModelClient
    from ads_booster.contracts.tools import ToolDescriptor
    from ads_booster.transport.json_types import JsonObject

_INDEX = """# KR 레퍼런스 인덱스

| id | outcome | relative | domain_cluster | why |
|---|---|---|---|---|
| kr-001 | hit | 175.30 | 직장인공감 | 한 문장 헤드라인 |
| kr-005 | hit | 0.06 | 1인개발_빌딩인퍼블릭 | 메이커 반전 공개 |
"""


@dataclass(slots=True)
class FakeModelClient:
    answers: list[str | Exception]
    histories: list[tuple[JsonObject, ...]] = field(default_factory=list)

    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        self.histories.append(history)
        assert tools == ()
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return ModelTurn(text=answer, calls=())


@dataclass(frozen=True, slots=True)
class FakeModelSource:
    client: FakeModelClient

    @contextmanager
    def open(self) -> Generator[ModelClient]:
        yield self.client


def _context(root: Path) -> Path:
    directory = root / "context"
    index = directory / "references" / "KR" / "INDEX.md"
    index.parent.mkdir(parents=True, exist_ok=True)
    _ = index.write_text(_INDEX, encoding="utf-8")
    return directory


def _answer(name: str = "이서진", count: int = 3) -> str:
    return json.dumps(
        [
            {
                "identity": {
                    "display_name": f"{name}{index}" if index else name,
                    "age": 27 + index,
                    "region": "서울 마포구",
                    "occupation": "병동 간호사",
                    "concept": "3교대를 잠금화면 일정으로 버티는 간호사",
                    "domain": "office_worker",
                    "interests": ["쿠로미", "필라테스", "동네 베이커리"],
                    "life_rhythm": "데이 근무일은 5시 40분 기상",
                    "taste": {
                        "background_subject": "character_other",
                        "background_mood": "파스텔 톤 캐릭터 화면",
                        "font": "sf_pro_rounded",
                    },
                },
                "reason": "kr-001처럼 직장인 공감이 도달을 만든 사례가 있다",
            }
            for index in range(count)
        ],
        ensure_ascii=False,
    )


def _generator(root: Path, client: FakeModelClient) -> AccountProposalGenerator:
    return AccountProposalGenerator(
        models=FakeModelSource(client),
        context_directory=_context(root),
        model="gpt-5.5",
    )


def _existing(store: SqliteWorkspaceStore, root: Path, name: str, occupation: str):  # noqa: ANN202
    workspace_id = store.create_workspace("Trace").workspace.workspace_id
    del root
    return store.create_account(
        workspace_id,
        MarketingAccountCreate(
            country="KR",
            identity=MarketingAccountIdentity(
                display_name=name,
                age=29,
                region="서울 성동구",
                occupation=occupation,
                concept="이미 운영 중인 컨셉",
                domain=CandidatePersonaDomain.SPORTS_FAN,
                interests=("KIA 타이거즈",),
                life_rhythm="평일 10시 출근",
                taste=MarketingAccountTaste(
                    background_subject=CandidateBackgroundSubject.SPORTS_TEAM,
                    background_mood="야간 경기 조명",
                    font=LockScreenFont.SF_PRO,
                ),
            ),
            schedule=MarketingAccountSchedule(language="ko", timezone="Asia/Seoul"),
        ),
    )


def test_a_proposal_carries_the_identity_and_the_evidence(tmp_path: Path) -> None:
    """A suggestion nobody can check is a suggestion nobody should act on."""
    # Given a model that answers with three accounts
    client = FakeModelClient([_answer()])

    # When proposals are asked for
    proposals = _generator(tmp_path, client).propose("KR")

    # Then each one is a whole identity plus the reference ids it argues from
    assert len(proposals) == 3
    assert proposals[0].identity.display_name == "이서진"
    assert proposals[0].identity.domain is CandidatePersonaDomain.OFFICE_WORKER
    assert proposals[0].identity.taste.font is LockScreenFont.SF_PRO_ROUNDED
    assert "kr-001" in proposals[0].reason


def test_the_prompt_stands_on_the_reference_index(tmp_path: Path) -> None:
    """The index is the only place that records which speaker types reached people."""
    # Given
    client = FakeModelClient([_answer()])

    # When the prompt is assembled
    instruction = _generator(tmp_path, client).instruction("KR")

    # Then the performance table is in it, framed as evidence about speaker types
    assert "| kr-001 | hit | 175.30 | 직장인공감" in instruction
    assert "domain_cluster는 화자 유형이고" in instruction
    # And the closed vocabularies the create form enforces are named
    assert "office_worker" in instruction
    assert "character_other" in instruction
    assert "sf_pro_rounded" in instruction


def test_the_prompt_refuses_maker_material_even_though_the_index_rewards_it(
    tmp_path: Path,
) -> None:
    """The index has maker hits; we cannot use them.

    An account field carrying 배포·코딩 words poisons the subject pool of every caption that
    account will ever write, so the ban is stated against the evidence rather than in spite
    of it.
    """
    # Given an index whose maker row is a hit
    client = FakeModelClient([_answer()])

    # When the prompt is assembled
    instruction = _generator(tmp_path, client).instruction("KR")

    # Then the ban names the temptation it is refusing
    assert "1인개발" in instruction
    assert "개발·메이커 소재를 제안하지 마세요" in instruction
    assert "소재 통이 오염됩니다" in instruction
    assert "직업이 개발자인 계정도 제안하지 마세요" in instruction


def test_the_prompt_lists_the_accounts_already_running_in_that_country(
    tmp_path: Path,
) -> None:
    """Suggesting a person who already posts wastes the reviewer's only decision."""
    # Given one Korean account already running
    store = SqliteWorkspaceStore(tmp_path)
    existing = _existing(store, tmp_path, "김도현", "백엔드 개발자")
    client = FakeModelClient([_answer()])

    # When the prompt is assembled with it
    instruction = _generator(tmp_path, client).instruction("KR", (existing,))

    # Then the running account is quoted back with what makes it distinct
    assert "- 김도현 (백엔드 개발자, sports_fan): 이미 운영 중인 컨셉" in instruction
    assert "도메인·직업·컨셉이 겹치지 않게 하세요" in instruction

    # And a workspace with nothing running in that country says so rather than listing
    # another country's accounts as if they were competition for this one.
    japanese = existing.model_copy(update={"country": "JP"})
    empty = _generator(tmp_path, FakeModelClient([_answer()])).instruction("KR", (japanese,))
    assert "아직 이 국가에 운영 중인 계정이 없습니다." in empty
    assert "김도현" not in empty


def test_a_missing_reference_index_names_the_document(tmp_path: Path) -> None:
    """A proposal with no evidence behind it is not worth answering."""
    # Given a context directory with no index for that country
    generator = _generator(tmp_path, FakeModelClient([_answer()]))

    # When / Then the absent document is named rather than silently skipped
    with pytest.raises(CandidateContextMissingError) as failure:
        _ = generator.propose("JP")
    assert "references/JP/INDEX.md" in failure.value.message


def test_typed_provider_failures_survive_as_themselves(tmp_path: Path) -> None:
    """The button has to be able to say what went wrong."""
    # Given three model failures
    fenced = _generator(tmp_path, FakeModelClient([f"```json\n{_answer()}\n```"]))
    malformed = _generator(tmp_path, FakeModelClient(["설명만 있고 JSON이 없습니다"]))
    unauthorised = _generator(
        tmp_path, FakeModelClient([OAuthError("auth_missing", "login required")])
    )
    offline = _generator(tmp_path, FakeModelClient([ProviderError("provider_network", "boom")]))

    # When / Then a fenced answer is still read, and each failure keeps its own type
    assert len(fenced.propose("KR")) == 3
    with pytest.raises(CandidateFormatError):
        _ = malformed.propose("KR")
    with pytest.raises(CandidateAuthRequiredError):
        _ = unauthorised.propose("KR")
    with pytest.raises(CandidateProviderError):
        _ = offline.propose("KR")
