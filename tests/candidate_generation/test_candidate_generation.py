from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from trace_capture.auth.codex import OAuthError
from trace_capture.candidate_generation import (
    REQUIRED_DOCUMENTS,
    CandidateAuthRequiredError,
    CandidateContextMissingError,
    CandidateContextSource,
    CandidateFormatError,
    CandidateGenerator,
    CandidateProviderError,
    build_instruction,
    default_context_directory,
    parse_candidate_drafts,
)
from trace_capture.providers.codex import ModelTurn
from trace_capture.providers.errors import ProviderError
from trace_capture.workspace import (
    CandidateBackgroundSubject,
    CandidateSource,
    CandidateStatus,
    SqliteWorkspaceStore,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence
    from pathlib import Path

    from trace_capture.agent.session import ModelClient
    from trace_capture.contracts.tools import ToolDescriptor
    from trace_capture.transport.json_types import JsonObject
    from trace_capture.workspace import WorkspaceId


def _draft(topic: str = "시험기간 일정 관리") -> dict[str, object]:
    return {
        "topic": topic,
        "country": "KR",
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
    return json.dumps([_draft(f"주제 {index}") for index in range(count)], ensure_ascii=False)


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
) -> CandidateGenerator:
    return CandidateGenerator(
        store=store,
        models=FakeModelSource(client),
        context_source=CandidateContextSource(_write_context(tmp_path)),
    )


def _workspace(store: SqliteWorkspaceStore) -> WorkspaceId:
    return store.create_workspace("Trace team").workspace.workspace_id


def test_context_directory_prefers_the_serve_workspace_when_present(tmp_path: Path) -> None:
    # Given
    local = _write_context(tmp_path)

    # When / Then
    assert default_context_directory(tmp_path) == local


def test_context_directory_falls_back_to_complete_packaged_context(tmp_path: Path) -> None:
    # Given / When
    directory = default_context_directory(tmp_path)
    bundle = CandidateContextSource(directory).load()

    # Then
    assert directory.name == "context"
    assert tuple(document.relative_path for document in bundle.documents) == REQUIRED_DOCUMENTS
    assert all(document.text.strip() for document in bundle.documents)


def test_context_directory_can_be_pointed_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("TRACE_AGENT_CONTEXT_DIR", str(tmp_path / "elsewhere"))

    # When / Then
    assert default_context_directory(tmp_path) == tmp_path / "elsewhere"


def test_missing_context_directory_explains_where_to_run_the_server(tmp_path: Path) -> None:
    # Given
    source = CandidateContextSource(tmp_path / "context")

    # When / Then
    with pytest.raises(CandidateContextMissingError) as failure:
        _ = source.load()
    assert "context 폴더를 찾을 수 없습니다" in failure.value.message
    assert "trace 폴더에서 서버를 실행했는지 확인하세요." in failure.value.message


def test_missing_context_file_names_the_file(tmp_path: Path) -> None:
    # Given
    directory = _write_context(tmp_path, skip=("core/FACTS.md", "references/KR/INDEX.md"))

    # When / Then
    with pytest.raises(CandidateContextMissingError) as failure:
        _ = CandidateContextSource(directory).load()
    assert failure.value.missing == ("core/FACTS.md", "references/KR/INDEX.md")
    assert "core/FACTS.md" in failure.value.message
    assert "references/KR/INDEX.md" in failure.value.message


def test_blank_context_file_counts_as_missing(tmp_path: Path) -> None:
    # Given
    directory = _write_context(tmp_path)
    _ = (directory / "core" / "VOICE-KR.md").write_text("   \n", encoding="utf-8")

    # When / Then
    with pytest.raises(CandidateContextMissingError) as failure:
        _ = CandidateContextSource(directory).load()
    assert failure.value.missing == ("core/VOICE-KR.md",)


def test_instruction_carries_every_document_and_the_hard_rules(tmp_path: Path) -> None:
    # Given
    bundle = CandidateContextSource(_write_context(tmp_path)).load()

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


def test_generation_includes_control_plane_run_context(tmp_path: Path) -> None:
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    client = FakeModelClient([_answer()])
    generator = _generator(tmp_path, store, client)

    created = generator.generate(
        workspace_id,
        run_context='{"shared_instruction":"계정별 실행 지침","private_memory":[]}',
    )

    assert len(created) == 3
    instruction = str(client.histories[0][0]["content"])
    assert "[control-plane 실행 컨텍스트]" in instruction
    assert "계정별 실행 지침" in instruction


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
    assert network_failure.value.message == "AI 요청에 실패했습니다 — 잠시 후 다시 시도해 주세요."
    assert "context 파일이 너무 커서" in overflow_failure.value.message
    assert store.list_candidates(workspace_id) == ()
