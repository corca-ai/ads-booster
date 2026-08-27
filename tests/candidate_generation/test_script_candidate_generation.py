from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import pytest

from ads_booster.auth.codex import OAuthError
from ads_booster.candidate_generation import (
    REQUIRED_DOCUMENTS,
    CandidateAuthRequiredError,
    CandidateContextMissingError,
    CandidateContextSource,
    CandidateDocument,
    CandidateFormatError,
    CandidateProviderError,
    ScriptCandidateGenerator,
    assign_domains,
    build_instruction,
    parse_candidate_drafts,
)
from ads_booster.providers.codex import ModelTurn
from ads_booster.providers.errors import ProviderError
from ads_booster.workspace import (
    CandidateBackgroundSubject,
    CandidateCreate,
    CandidateHistoryEntry,
    CandidateImageInputs,
    CandidatePersonaDomain,
    CandidateSource,
    CandidateStatus,
    SqliteWorkspaceStore,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence
    from pathlib import Path

    from ads_booster.agent.session import ModelClient
    from ads_booster.contracts.tools import ToolDescriptor
    from ads_booster.transport.json_types import JsonObject
    from ads_booster.workspace import WorkspaceId


# The generator assigns the least-covered domains; with the identity shuffle below and an
# empty workspace that is the first three of the vocabulary, in declaration order.
_ASSIGNED: Final = ("sports_fan", "idol_fandom", "exam_prepper")


def _identity(
    domains: Sequence[CandidatePersonaDomain],
) -> Sequence[CandidatePersonaDomain]:
    return tuple(domains)


def _draft(topic: str = "시험기간 일정 관리", domain: str = "sports_fan") -> dict[str, object]:
    return {
        "topic": topic,
        "country": "KR",
        "persona_domain": domain,
        "caption": f"{topic} — 잠금화면부터 바꾼다",
        "hypothesis": "1인칭 감탄이 저장률을 올린다",
        "refs_used": ["kr-001"],
        "principles_applied": [1, 4],
        "appium_prompt": "입력_일정: 9시 스터디\n기기_시각: 07:20",
        "image_inputs": {
            "trace_items": ["09:00 통계학 2교시", "13:00 스터디", "19:00 러닝"],
            "device_time": "07:20",
            "background_subject": "scenery",
            "background_mood": "늦은 밤 책상 위 스탠드 불빛",
            "language": "ko",
        },
    }


def _answer(count: int = 3) -> str:
    return json.dumps(
        [_draft(f"주제 {index}", _ASSIGNED[index % len(_ASSIGNED)]) for index in range(count)],
        ensure_ascii=False,
    )


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


def _write_context(root: Path, *, skip: Sequence[str] = ()) -> Path:
    directory = root / "context"
    for relative_path in REQUIRED_DOCUMENTS:
        if relative_path in skip:
            continue
        path = directory / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(f"# {relative_path}\n내용", encoding="utf-8")
    return directory


def _generator(
    tmp_path: Path,
    store: SqliteWorkspaceStore,
    client: FakeModelClient,
) -> ScriptCandidateGenerator:
    return ScriptCandidateGenerator(
        store=store,
        models=FakeModelSource(client),
        context_source=CandidateContextSource(
            _write_context(tmp_path), required=REQUIRED_DOCUMENTS
        ),
        model="gpt-5.5",
        shuffle=_identity,
    )


def _manual_create(
    workspace_id: WorkspaceId,
    domain: CandidatePersonaDomain | None,
    *,
    source: CandidateSource,
    topic: str = "시험기간 일정 관리",
) -> CandidateCreate:
    """One stored candidate, used to seed coverage counts and history."""
    return CandidateCreate(
        workspace_id=workspace_id,
        source=source,
        country="KR",
        topic=topic,
        persona_domain=domain,
        caption="캡션",
        hypothesis="가설",
        image_inputs=CandidateImageInputs(
            trace_items=("09:00 통계학 2교시",),
            device_time="07:20",
            background_subject=CandidateBackgroundSubject.SCENERY,
            background_mood="늦은 밤 책상 위 스탠드 불빛",
            language="ko",
        ),
    )


def _workspace(store: SqliteWorkspaceStore) -> WorkspaceId:
    return store.create_workspace("Trace team").workspace.workspace_id


def test_missing_context_file_names_the_file(tmp_path: Path) -> None:
    # Given
    directory = _write_context(tmp_path, skip=("core/FACTS.md", "references/KR/INDEX.md"))

    # When / Then
    with pytest.raises(CandidateContextMissingError) as failure:
        _ = CandidateContextSource(directory, required=REQUIRED_DOCUMENTS).load()
    assert failure.value.missing == ("core/FACTS.md", "references/KR/INDEX.md")
    assert "core/FACTS.md" in failure.value.message
    assert "references/KR/INDEX.md" in failure.value.message


def test_blank_context_file_counts_as_missing(tmp_path: Path) -> None:
    # Given
    directory = _write_context(tmp_path)
    _ = (directory / "core" / "VOICE-KR.md").write_text("   \n", encoding="utf-8")

    # When / Then
    with pytest.raises(CandidateContextMissingError) as failure:
        _ = CandidateContextSource(directory, required=REQUIRED_DOCUMENTS).load()
    assert failure.value.missing == ("core/VOICE-KR.md",)


def test_instruction_carries_every_document_and_the_hard_rules(tmp_path: Path) -> None:
    # Given
    bundle = CandidateContextSource(_write_context(tmp_path), required=REQUIRED_DOCUMENTS).load()

    # When
    instruction = build_instruction(bundle, count=3)

    # Then
    for relative_path in REQUIRED_DOCUMENTS:
        assert f"[context 문서: {relative_path}]" in instruction
    assert "FACTS 문서에 없는 검증 가능한 사실을 주장하지 마세요." in instruction
    assert "면책성 괄호 문구" in instruction
    assert "VOICE 문서를 그대로 따르세요" in instruction
    assert "INDEX 문서에 실제로 존재하는 id만" in instruction
    assert "appium_prompt" in instruction
    assert "정확히 3개의 객체" in instruction
    assert "image_inputs" in instruction
    assert "character_kitty" in instruction
    assert "sports_team" in instruction
    assert "5~7개를 권장합니다" in instruction
    assert "모호어 대신 실제로 보이는 것을" in instruction
    assert "실제로 잠금화면에 설정해뒀을 법한 배경" in instruction


def _assert_persona_specificity_block(instruction: str) -> None:
    assert "[페르소나 구체성 규칙]" in instruction
    assert '서로 다른 "구체 정체성"을 먼저 창작하고' in instruction
    assert "도메인을 스포츠에 몰지 말고 넓게 흩으세요" in instruction
    assert '"야구를 좋아함", "운동을 좋아함" 수준은 금지입니다.' in instruction
    assert "어느 팀의 팬인지까지 정해진" in instruction
    assert "그 정체성의 실제 일주일에서 나올 법한 문자열로" in instruction
    assert "기아 vs LG 18:30 직관" in instruction
    assert '"회의", "운동", "공부", "약속" 같은 범용 일정은 금지입니다.' in instruction
    assert "그 정체성의 생활 리듬과 맞아야 합니다" in instruction
    assert (
        "실존 인물명·캐릭터명·팀명을 쓰는 자리는 image_inputs.background_search_query 하나뿐입니다."
        in instruction
    )
    assert "background_mood와 topic에는 넣지 마세요" in instruction
    assert "캡션의 화자도 같은 정체성이어야 합니다" in instruction
    assert "데모 프레임 동사로 드러내고" in instruction


def test_instruction_sanctions_real_names_only_in_the_background_search_query(
    tmp_path: Path,
) -> None:
    """The model must author the search query, and it is the one field real names belong in."""
    # Given
    bundle = CandidateContextSource(_write_context(tmp_path), required=REQUIRED_DOCUMENTS).load()

    # When
    instruction = build_instruction(bundle, count=3)

    # Then the rule block asks for a wallpaper, not an occupation scene, and allows proper
    # nouns there
    assert '"그 사람이 자기 폰 배경화면으로 저장해뒀을 사진"을 찾는 검색어' in instruction
    assert (
        "이 필드에 한해 실존 인물명·캐릭터명·팀명·아이돌 그룹명을 그대로 써도 됩니다."
        in instruction
    )
    assert '"김도영 직캠"' in instruction
    assert '"쿠로미 배경화면"' in instruction
    # And the persona block no longer carries the blanket ban it used to contradict
    assert "이미지 검색 질의로 쓰이는 필드에는" not in instruction
    assert "실존 인물명·캐릭터명·브랜드 로고명을 넣지 마세요" not in instruction
    # And the output contract names the field so the model actually emits it
    assert '"background_search_query"' in instruction
    assert (
        "- background_search_query: 그 사람이 배경화면으로 저장했을 사진을 찾을 검색어"
        in instruction
    )


def test_instruction_carries_the_persona_specificity_block(tmp_path: Path) -> None:
    """The block reaches the model on the plain path, with the INDEX but no reference bodies."""
    # Given
    bundle = CandidateContextSource(_write_context(tmp_path), required=REQUIRED_DOCUMENTS).load()

    # When
    instruction = build_instruction(bundle, count=3)

    # Then
    _assert_persona_specificity_block(instruction)


def test_persona_specificity_block_survives_added_reference_bodies(tmp_path: Path) -> None:
    """Extra reference documents in the bundle must not push the block out of the prompt."""
    # Given
    loaded = CandidateContextSource(_write_context(tmp_path), required=REQUIRED_DOCUMENTS).load()
    bundle = loaded.model_copy(
        update={
            "documents": (
                *loaded.documents,
                CandidateDocument(relative_path="references/KR/kr-001.md", text="# kr-001\n본문"),
            )
        }
    )

    # When
    instruction = build_instruction(bundle, count=3)

    # Then
    assert "[context 문서: references/KR/kr-001.md]" in instruction
    _assert_persona_specificity_block(instruction)


def test_fenced_json_is_parsed() -> None:
    # Given
    fenced = f"```json\n{_answer()}\n```"

    # When
    drafts = parse_candidate_drafts(fenced, expected=3, country="KR")

    # Then
    assert len(drafts) == 3
    assert drafts[0].appium_prompt.startswith("입력_일정")


def test_parse_rejects_a_wrong_count_and_a_wrong_country() -> None:
    # Given
    two_items = json.dumps([_draft(), _draft("다른 주제")], ensure_ascii=False)
    japanese = json.dumps([{**_draft(), "country": "JP"}], ensure_ascii=False)

    # When / Then
    with pytest.raises(CandidateFormatError) as count_failure:
        _ = parse_candidate_drafts(two_items, expected=3, country="KR")
    with pytest.raises(CandidateFormatError) as country_failure:
        _ = parse_candidate_drafts(japanese, expected=1, country="KR")
    assert count_failure.value.detail == "후보 3개가 필요하지만 2개를 받았습니다."
    assert "country는 모두 KR" in country_failure.value.detail
    assert (
        count_failure.value.message == "AI 응답이 형식을 통과하지 못했습니다 — 다시 시도해 주세요."
    )


def test_parse_rejects_unusable_image_inputs() -> None:
    # Given answers whose image inputs break the machine contract
    def one(image_inputs: dict[str, object]) -> str:
        return json.dumps([{**_draft(), "image_inputs": image_inputs}], ensure_ascii=False)

    base = _draft()["image_inputs"]
    assert isinstance(base, dict)
    bad_time = one({**base, "device_time": "7시 20분"})
    unknown_subject = one({**base, "background_subject": "감성적인 무언가"})
    nine_items = one({**base, "trace_items": [f"일정 {index}" for index in range(9)]})
    no_items = one({**base, "trace_items": []})

    # When / Then
    for payload, rejected_field in (
        (bad_time, "device_time"),
        (unknown_subject, "background_subject"),
        (nine_items, "trace_items"),
        (no_items, "trace_items"),
    ):
        with pytest.raises(CandidateFormatError) as failure:
            _ = parse_candidate_drafts(payload, expected=1, country="KR")
        assert rejected_field in failure.value.detail


def test_parse_accepts_five_to_seven_schedule_items() -> None:
    # Given answers at the recommended schedule lengths
    base = _draft()["image_inputs"]
    assert isinstance(base, dict)

    # When / Then
    for count in (5, 6, 7):
        items = [f"{index:02d}:00 일정" for index in range(count)]
        payload = json.dumps(
            [{**_draft(), "image_inputs": {**base, "trace_items": items}}],
            ensure_ascii=False,
        )
        drafts = parse_candidate_drafts(payload, expected=1, country="KR")
        assert len(drafts[0].image_inputs.trace_items) == count


def test_malformed_image_inputs_are_retried_once(tmp_path: Path) -> None:
    # Given a first answer whose background subject is not in the vocabulary
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    base = _draft()["image_inputs"]
    assert isinstance(base, dict)
    invalid = json.dumps(
        [
            {**_draft(f"주제 {index}"), "image_inputs": {**base, "background_subject": "예쁜 배경"}}
            for index in range(3)
        ],
        ensure_ascii=False,
    )
    client = FakeModelClient([invalid, _answer()])
    generator = _generator(tmp_path, store, client)

    # When
    created = generator.generate(workspace_id)

    # Then
    assert len(created) == 3
    assert created[0].image_inputs is not None
    assert created[0].image_inputs.background_subject is CandidateBackgroundSubject.SCENERY
    retry_turn = client.histories[1][-1]
    assert "background_subject" in str(retry_turn["content"])


def test_generation_stores_three_automatic_candidates(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    client = FakeModelClient([_answer()])
    generator = _generator(tmp_path, store, client)

    # When
    created = generator.generate(workspace_id)

    # Then
    assert len(created) == 3
    assert all(record.source is CandidateSource.AUTO for record in created)
    assert all(record.status is CandidateStatus.AWAITING_REVIEW for record in created)
    assert created[0].shooting_order.startswith("입력_일정")
    assert created[0].refs_used == ("kr-001",)
    assert len(store.list_candidates(workspace_id)) == 3
    assert len(client.histories) == 1


def test_generation_records_what_the_run_read_on_every_candidate(tmp_path: Path) -> None:
    # Given a context directory whose documents the run will assemble
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    client = FakeModelClient([_answer()])
    generator = _generator(tmp_path, store, client)
    directory = _write_context(tmp_path)

    # When the batch is generated
    created = generator.generate(workspace_id)

    # Then every candidate of the batch carries the same recorded provenance
    provenances = [record.generation_provenance for record in created]
    assert all(provenance == provenances[0] for provenance in provenances)
    provenance = provenances[0]
    assert provenance is not None
    assert tuple(document.relative_path for document in provenance.documents) == REQUIRED_DOCUMENTS
    assert [document.size_bytes for document in provenance.documents] == [
        (directory / relative_path).stat().st_size for relative_path in REQUIRED_DOCUMENTS
    ]
    assert provenance.model == "gpt-5.5"
    assert provenance.instruction_chars == len(str(client.histories[0][0]["content"]))
    assert provenance.generated_at > 0

    # And it survives the store round trip
    stored = store.get_candidate(workspace_id, created[0].candidate_id)
    assert stored.generation_provenance == provenance


def test_one_malformed_answer_is_retried_once_with_the_validation_error(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    client = FakeModelClient(["설명만 있고 JSON이 없습니다", _answer()])
    generator = _generator(tmp_path, store, client)

    # When
    created = generator.generate(workspace_id)

    # Then
    assert len(created) == 3
    assert len(client.histories) == 2
    retry_turn = client.histories[1][-1]
    assert "직전 응답은 형식 검증을 통과하지 못했습니다." in str(retry_turn["content"])
    assert "JSON 파싱 실패" in str(retry_turn["content"])


def test_two_malformed_answers_store_nothing(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    client = FakeModelClient(["nope", _answer(2)])
    generator = _generator(tmp_path, store, client)

    # When / Then
    with pytest.raises(CandidateFormatError) as failure:
        _ = generator.generate(workspace_id)
    assert failure.value.message == "AI 응답이 형식을 통과하지 못했습니다 — 다시 시도해 주세요."
    assert store.list_candidates(workspace_id) == ()


def test_missing_credential_tells_the_operator_to_log_in(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    client = FakeModelClient([OAuthError("auth_missing", "OpenAI OAuth login is required")])
    generator = _generator(tmp_path, store, client)

    # When / Then
    with pytest.raises(CandidateAuthRequiredError) as failure:
        _ = generator.generate(workspace_id)
    assert "trace-agent auth login" in failure.value.message
    assert store.list_candidates(workspace_id) == ()


def test_provider_failure_and_context_overflow_have_separate_messages(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    network = _generator(
        tmp_path,
        store,
        FakeModelClient([ProviderError("provider_network", "boom")]),
    )
    overflow = _generator(
        tmp_path,
        store,
        FakeModelClient([ProviderError("provider_http", "too long", context_overflow=True)]),
    )

    # When / Then
    with pytest.raises(CandidateProviderError) as network_failure:
        _ = network.generate(workspace_id)
    with pytest.raises(CandidateProviderError) as overflow_failure:
        _ = overflow.generate(workspace_id)
    assert network_failure.value.message == (
        "AI 요청에 실패했습니다 (provider_network) — 잠시 후 다시 시도해 주세요."
    )
    assert "context 파일이 너무 커서" in overflow_failure.value.message
    assert store.list_candidates(workspace_id) == ()


def test_instruction_forbids_occupation_still_life_queries_by_example(tmp_path: Path) -> None:
    """The live failures were job-scene queries, so both are named as what not to write."""
    # Given
    bundle = CandidateContextSource(_write_context(tmp_path), required=REQUIRED_DOCUMENTS).load()

    # When
    instruction = build_instruction(bundle, count=3)

    # Then the two queries that actually failed are quoted as bad examples
    assert "직업 소품·작업 공간·물건 나열형 검색어는 금지입니다" in instruction
    assert '"병원 사물함 간호사 명찰 볼펜 사진"' in instruction
    assert '"개인 카페 에스프레소 머신 새벽 불빛"' in instruction

    # And a nurse persona is shown choosing a wallpaper rather than a hospital photo
    assert '간호사 페르소나 → "고양이 배경화면 고화질"' in instruction

    # And the query must agree with the subject token that was chosen
    assert "background_subject와 정합해야 합니다" in instruction
    assert "scenery면 풍경, pet이면 반려동물" in instruction

    # And the division of labour is stated: the schedule carries the job, the wallpaper the taste
    assert "직업과 하루는 trace_items와 캡션이 드러냅니다" in instruction
    assert (
        "간호사라고 해서 병원 사진, 카페 사장이라고 해서 카페 사진을 깔지 않습니다" in instruction
    )

    # And the worked example is no longer an occupation still-life
    assert "늦은 밤 원룸 책상 스탠드 불빛 사진" not in instruction
    assert '"background_search_query": "제주 바다 노을 배경화면 고화질"' in instruction


def test_assignment_picks_the_least_covered_domains(tmp_path: Path) -> None:
    """Coverage decides the batch, so the genres nobody has written go first."""
    # Given a workspace whose generated candidates lean on three domains
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    for domain in ("sports_fan", "sports_fan", "idol_fandom", "exam_prepper"):
        _ = store.create_candidate(
            _manual_create(
                workspace_id, CandidatePersonaDomain(domain), source=CandidateSource.AUTO
            )
        )

    # When the next batch is assigned with a deterministic tie-break
    assigned = assign_domains(store.count_candidate_domains(workspace_id), 3, _identity)

    # Then none of the covered domains is picked while untouched ones remain
    assert CandidatePersonaDomain.SPORTS_FAN not in assigned
    assert CandidatePersonaDomain.IDOL_FANDOM not in assigned
    assert CandidatePersonaDomain.EXAM_PREPPER not in assigned
    assert len(assigned) == 3


def test_assignment_breaks_ties_with_the_injected_shuffle() -> None:
    """Every domain at zero is a tie, and the tie-break is the only thing left to decide it."""

    # Given a cold workspace and a shuffle that reverses the vocabulary
    def _reversed(
        domains: Sequence[CandidatePersonaDomain],
    ) -> Sequence[CandidatePersonaDomain]:
        return tuple(reversed(list(domains)))

    # When two assignments are made from the same empty counts
    forward = assign_domains({}, 3, _identity)
    backward = assign_domains({}, 3, _reversed)

    # Then the order of the tie follows the shuffle, not the declaration order
    assert forward == tuple(CandidatePersonaDomain)[:3]
    assert backward == tuple(reversed(list(CandidatePersonaDomain)))[:3]


def test_counts_still_win_over_the_shuffle() -> None:
    """A shuffle may reorder equals; it must never promote a covered domain over an empty one."""
    # Given one domain that already has rows, and a shuffle that would put it first
    counts = {CandidatePersonaDomain.SMALL_BUSINESS.value: 5}

    def _business_first(
        domains: Sequence[CandidatePersonaDomain],
    ) -> Sequence[CandidatePersonaDomain]:
        rest = [d for d in domains if d is not CandidatePersonaDomain.SMALL_BUSINESS]
        return (CandidatePersonaDomain.SMALL_BUSINESS, *rest)

    # When the batch is assigned
    assigned = assign_domains(counts, 3, _business_first)

    # Then the covered domain is still last in line
    assert CandidatePersonaDomain.SMALL_BUSINESS not in assigned


def test_the_instruction_binds_one_candidate_to_one_domain(tmp_path: Path) -> None:
    # Given a batch assignment
    bundle = CandidateContextSource(_write_context(tmp_path), required=REQUIRED_DOCUMENTS).load()

    # When the instruction is built
    instruction = build_instruction(
        bundle,
        count=3,
        domains=(
            CandidatePersonaDomain.PET_OWNER,
            CandidatePersonaDomain.SMALL_BUSINESS,
            CandidatePersonaDomain.EXAM_PREPPER,
        ),
    )

    # Then each candidate is bound to its own domain by position, not handed a pool
    assert "후보 1: pet_owner (반려동물 보호자) 도메인의 구체 정체성으로" in instruction
    assert "후보 2: small_business (자영업) 도메인의 구체 정체성으로" in instruction
    assert "후보 3: exam_prepper (수험생) 도메인의 구체 정체성으로" in instruction
    assert "한 도메인에 후보를 몰아넣지 마세요" in instruction
    assert '"persona_domain": "배정받은 도메인 토큰"' in instruction


def test_the_instruction_lists_recent_candidates_to_avoid_repeating(tmp_path: Path) -> None:
    # Given a workspace that has already produced candidates
    bundle = CandidateContextSource(_write_context(tmp_path), required=REQUIRED_DOCUMENTS).load()
    history = (
        CandidateHistoryEntry(
            persona_domain=CandidatePersonaDomain.SPORTS_FAN, topic="기아 직관 준비"
        ),
        CandidateHistoryEntry(persona_domain=None, topic="레거시 후보"),
    )

    # When the instruction is built
    instruction = build_instruction(bundle, count=3, history=history)

    # Then the model is shown what not to repeat, legacy rows included
    assert "[최근 생성된 후보 목록]" in instruction
    assert "소재·정체성이 겹치지 않게" in instruction
    assert "- [스포츠 팬] 기아 직관 준비" in instruction
    assert "- [도메인 미기록] 레거시 후보" in instruction


def test_an_unassigned_domain_in_the_answer_is_retried_once(tmp_path: Path) -> None:
    # Given a first answer that ignores the assignment and writes its favourite domain
    wrong = json.dumps(
        [_draft(f"주제 {index}", "sports_fan") for index in range(3)], ensure_ascii=False
    )
    client = FakeModelClient([wrong, _answer()])
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)

    # When the batch is generated
    records = _generator(tmp_path, store, client).generate(workspace_id)

    # Then the retry turn quoted the binding back and the second answer was accepted
    retry = client.histories[1][-1]["content"]
    assert isinstance(retry, str)
    assert "persona_domain은 배정된 순서대로" in retry
    assert [record.persona_domain for record in records] == [
        CandidatePersonaDomain(domain) for domain in _ASSIGNED
    ]


def test_the_batch_records_which_domains_it_was_assigned(tmp_path: Path) -> None:
    # Given a generated batch
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)

    # When it runs
    records = _generator(tmp_path, store, FakeModelClient([_answer()])).generate(workspace_id)

    # Then the assignment is on every candidate's provenance for the panel to render
    provenance = records[0].generation_provenance
    assert provenance is not None
    assert provenance.assigned_domains == tuple(
        CandidatePersonaDomain(domain) for domain in _ASSIGNED
    )


def test_generated_history_is_read_back_newest_first_and_manual_rows_are_ignored(
    tmp_path: Path,
) -> None:
    # Given one manual candidate and two generated ones
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    _ = store.create_candidate(
        _manual_create(workspace_id, None, source=CandidateSource.MANUAL, topic="수동 후보")
    )
    _ = store.create_candidate(
        _manual_create(
            workspace_id,
            CandidatePersonaDomain.PET_OWNER,
            source=CandidateSource.AUTO,
            topic="첫 생성",
        )
    )
    _ = store.create_candidate(
        _manual_create(
            workspace_id,
            CandidatePersonaDomain.PARENTING,
            source=CandidateSource.AUTO,
            topic="두번째 생성",
        )
    )

    # When the next batch reads the history
    history = store.recent_candidate_history(workspace_id, 15)

    # Then only generated rows appear, newest first
    assert [entry.topic for entry in history] == ["두번째 생성", "첫 생성"]
    assert history[0].persona_domain is CandidatePersonaDomain.PARENTING


def test_the_persona_examples_are_marked_as_format_only(tmp_path: Path) -> None:
    """The concrete names kept coming back verbatim, so every example block disclaims itself."""
    # Given
    bundle = CandidateContextSource(_write_context(tmp_path), required=REQUIRED_DOCUMENTS).load()

    # When
    instruction = build_instruction(bundle, count=3)

    # Then the disclaimer sits with each block that carries a team, a person, or a character
    isolation = (
        "위 예시는 형식 참고용입니다. 예시의 팀·인물·캐릭터를 그대로 쓰지 말고 새로 정하세요."
    )
    assert instruction.count(isolation) == 3
