from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from PIL import Image
from pydantic import TypeAdapter

from ads_booster.candidate_generation import (
    CandidateImageOptions,
    CandidateImageStageError,
    LocalCandidateImageRunner,
    build_background_query,
    build_local_candidate_image_runner,
)
from ads_booster.candidate_generation.background_factory import ProductionCandidateBackgrounds
from ads_booster.candidate_generation.background_selection import (
    EXHAUSTED_CODE,
    JUDGE_REJECTED_CODE,
    JudgedBackground,
    JudgedBackgroundSelector,
)
from ads_booster.candidate_generation.factory import (
    COMPONENT_FIXTURE_ENVIRONMENT,
    IPHONE_UI_ENVIRONMENT,
)
from ads_booster.config.settings import AgentSettings
from ads_booster.default_assets import default_iphone_ui_path, default_trace_components_path
from ads_booster.search.image.background import BackgroundSearchError, SearchedBackground
from ads_booster.search.image.open_background import OpenWebBackgroundFetcher
from ads_booster.workspace import (
    CandidateBackgroundGrade,
    CandidateBackgroundGrades,
    CandidateBackgroundJudgment,
    CandidateBackgroundReview,
    CandidateBackgroundSubject,
    CandidateCreate,
    CandidateImageInputs,
    CandidateSource,
    CandidateStatus,
    SqliteWorkspaceStore,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from ads_booster.candidate_generation.background_judge import JudgePersona
    from ads_booster.candidate_generation.local_image_runner import CandidateBackgroundPort
    from ads_booster.workspace import CandidateRecord, WorkspaceId


_SIZE = (12, 26)


def _background_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", _SIZE, (20, 30, 40)).save(path, format="PNG")
    return path


def _component_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    layer = Image.new("RGBA", _SIZE, (0, 0, 0, 0))
    layer.putpixel((0, 0), (0, 255, 0, 255))
    layer.save(path, format="PNG")
    return path


def _ui_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    layer = Image.new("RGB", _SIZE, (0, 0, 0))
    layer.putpixel((1, 1), (255, 255, 255))
    layer.save(path, format="PNG")
    return path


@dataclass(slots=True)
class FakeBackgroundFetcher:
    """Stands in for the judged background selector, recording the queries it receives."""

    failure: Exception | None = None
    queries: list[str] = field(default_factory=list)
    personas: list[JudgePersona] = field(default_factory=list)

    def select(self, persona: JudgePersona, destination: Path) -> JudgedBackground:
        self.queries.append(persona.query)
        self.personas.append(persona)
        if self.failure is not None:
            raise self.failure
        _ = _background_png(destination)
        return JudgedBackground(
            background=SearchedBackground(
                path=destination,
                sha256=sha256(destination.read_bytes()).hexdigest(),
                query=persona.query,
                provider="duckduckgo",
                image_url="https://images.pexels.com/photos/1/night-desk.jpeg",
                source_url="https://www.pexels.com/photo/night-desk-1/",
            ),
            judgment=_judgment(),
        )


def _judgment() -> CandidateBackgroundJudgment:
    return CandidateBackgroundJudgment(
        reviews=(
            CandidateBackgroundReview(
                image_id="img-a",
                image_url="https://images.pexels.com/photos/1/night-desk.jpeg",
                source_url="https://www.pexels.com/photo/night-desk-1/",
                gated=False,
                grades=CandidateBackgroundGrades(
                    authenticity=CandidateBackgroundGrade.HIGH,
                    persona_fit=CandidateBackgroundGrade.HIGH,
                    background_fit=CandidateBackgroundGrade.MID,
                ),
                score=8,
                note="직접 찍은 책상 사진처럼 보입니다",
            ),
        ),
        chosen_id="img-a",
        reason="직접 찍은 책상 사진처럼 보입니다",
        model="gpt-5.5",
        query="쿠로미 배경화면 고화질",
    )


@dataclass(frozen=True, slots=True)
class FakeBackgroundSource:
    fetcher: FakeBackgroundFetcher

    @contextmanager
    def open(self) -> Generator[CandidateBackgroundPort]:
        yield self.fetcher


def _image_inputs(*, search_query: str | None = None) -> CandidateImageInputs:
    return CandidateImageInputs(
        trace_items=("09:00 통계학 2교시", "13:00 스터디", "19:00 러닝"),
        device_time="07:20",
        background_subject=CandidateBackgroundSubject.SCENERY,
        background_mood="늦은 밤 책상 위 스탠드 불빛",
        language="ko",
        background_search_query=search_query,
    )


def _approved_candidate(
    store: SqliteWorkspaceStore,
    workspace_id: WorkspaceId,
    *,
    inputs: CandidateImageInputs | None = None,
) -> CandidateRecord:
    created = store.create_candidate(
        CandidateCreate(
            workspace_id=workspace_id,
            source=CandidateSource.AUTO,
            country="KR",
            topic="시험기간 일정 관리",
            caption="시험 기간엔 잠금화면부터 바꾼다",
            hypothesis="1인칭 감탄이 저장률을 올린다",
            image_inputs=_image_inputs() if inputs is None else inputs,
        )
    )
    return store.review_candidate(
        workspace_id,
        created.candidate_id,
        accepted=True,
        note=None,
        expected_revision=created.revision,
    )


def _runner(
    tmp_path: Path,
    store: SqliteWorkspaceStore,
    fetcher: FakeBackgroundFetcher,
    *,
    fixture: Path | None = None,
) -> LocalCandidateImageRunner:
    return LocalCandidateImageRunner(
        store=store,
        backgrounds=FakeBackgroundSource(fetcher),
        options=CandidateImageOptions(
            home=tmp_path,
            component_fixture=(
                _component_png(tmp_path / "assets" / "components.png")
                if fixture is None
                else fixture
            ),
            iphone_ui_path=_ui_png(tmp_path / "assets" / "iphone-ui.png"),
        ),
    )


def _workspace(store: SqliteWorkspaceStore) -> WorkspaceId:
    return store.create_workspace("Trace team").workspace.workspace_id


def test_background_query_falls_back_to_the_mechanical_builder_on_legacy_rows(
    tmp_path: Path,
) -> None:
    # Given a candidate stored before the model authored its own search query
    store = SqliteWorkspaceStore(tmp_path)
    candidate = _approved_candidate(store, _workspace(store))
    assert candidate.image_inputs is not None
    assert candidate.image_inputs.background_search_query is None

    # When
    query = build_background_query(candidate)

    # Then
    assert "natural scenery landscape photo" in query
    assert "늦은 밤 책상 위 스탠드 불빛" in query
    assert "시험기간 일정 관리" in query
    assert "KR" in query
    assert "no text no logo no phone no UI" in query


def test_background_query_uses_the_model_authored_query_verbatim(tmp_path: Path) -> None:
    # Given a candidate whose model authored a concrete scene phrase with a real name in it
    store = SqliteWorkspaceStore(tmp_path)
    candidate = _approved_candidate(
        store,
        _workspace(store),
        inputs=_image_inputs(search_query="  김도영 타격 직캠  "),
    )

    # When
    query = build_background_query(candidate)

    # Then the mechanical wallpaper scaffolding never touches it
    assert query == "김도영 타격 직캠"


def test_a_composed_candidate_records_where_its_background_came_from(tmp_path: Path) -> None:
    # Given an approved candidate with a model-authored background query
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = _approved_candidate(
        store, workspace_id, inputs=_image_inputs(search_query="쿠로미 배경화면 고화질")
    )

    # When the image stage composes it
    reviewed = _runner(tmp_path, store, FakeBackgroundFetcher()).generate(
        workspace_id, candidate.candidate_id
    )

    # Then the reviewer can see the query, the provider, and the page that published it
    provenance = reviewed.background_provenance
    assert provenance is not None
    assert provenance.query == "쿠로미 배경화면 고화질"
    assert provenance.provider == "duckduckgo"
    assert provenance.source_url == "https://www.pexels.com/photo/night-desk-1/"
    assert len(provenance.sha256) == 64
    assert store.get_candidate(workspace_id, candidate.candidate_id) == reviewed

    # And the judgement that chose it survives the round trip through the store
    judgment = provenance.judgment
    assert judgment is not None
    assert judgment.chosen_id == "img-a"
    assert judgment.reason == "직접 찍은 책상 사진처럼 보입니다"
    assert judgment.reviews[0].score == 8


def test_the_production_candidate_path_judges_open_web_results(tmp_path: Path) -> None:
    # Given the production background source for the candidate path
    settings = AgentSettings(workspace=tmp_path, model="gpt-5", browser_command=("agent-browser",))

    # When one image run opens its selector
    with ProductionCandidateBackgrounds(settings).open() as selector:
        # Then the open-web fetcher only collects, and the judge picks what is used
        assert isinstance(selector, JudgedBackgroundSelector)
        assert isinstance(selector.fetcher, OpenWebBackgroundFetcher)
        assert selector.model == "gpt-5"


def test_successful_run_composes_an_image_and_moves_to_image_review(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = _approved_candidate(store, workspace_id)
    fetcher = FakeBackgroundFetcher()

    # When
    reviewed = _runner(tmp_path, store, fetcher).generate(workspace_id, candidate.candidate_id)

    # Then
    assert reviewed.status is CandidateStatus.IMAGE_AWAITING_REVIEW
    assert reviewed.image_path is not None
    assert reviewed.image_sha256 is not None
    composed = tmp_path / reviewed.image_path
    assert composed.is_file()
    assert sha256(composed.read_bytes()).hexdigest() == reviewed.image_sha256
    assert reviewed.revision == candidate.revision + 1
    assert len(fetcher.queries) == 1
    assert store.get_candidate(workspace_id, candidate.candidate_id) == reviewed


def test_a_candidate_awaiting_caption_review_cannot_be_composed(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    created = store.create_candidate(
        CandidateCreate(
            workspace_id=workspace_id,
            source=CandidateSource.MANUAL,
            country="KR",
            topic="아직 검수 전",
            caption="캡션",
            hypothesis="가설",
            image_inputs=_image_inputs(),
        )
    )
    fetcher = FakeBackgroundFetcher()

    # When / Then
    with pytest.raises(CandidateImageStageError) as failure:
        _ = _runner(tmp_path, store, fetcher).generate(workspace_id, created.candidate_id)
    assert "캡션·주제 승인을 마친 후보만" in failure.value.message
    assert fetcher.queries == []
    assert store.get_candidate(workspace_id, created.candidate_id).status is (
        CandidateStatus.AWAITING_REVIEW
    )


def test_a_missing_component_fixture_stops_the_run_before_the_model(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = _approved_candidate(store, workspace_id)
    fetcher = FakeBackgroundFetcher()
    runner = _runner(tmp_path, store, fetcher, fixture=tmp_path / "assets" / "absent.png")

    # When / Then
    with pytest.raises(CandidateImageStageError) as failure:
        _ = runner.generate(workspace_id, candidate.candidate_id)
    assert "잠금화면 부품 이미지를 찾을 수 없습니다" in failure.value.message
    assert (
        "환경변수 TRACE_AGENT_TRACE_COMPONENTS 에 설정한 경로가 존재하는지" in failure.value.message
    )
    assert "trace 폴더에서 서버를 실행했는지" not in failure.value.message
    assert fetcher.queries == []
    assert store.get_candidate(workspace_id, candidate.candidate_id) == candidate


def test_an_exhausted_background_search_leaves_the_candidate_caption_approved(
    tmp_path: Path,
) -> None:
    # Given a search that finds nothing on the approved providers
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = _approved_candidate(store, workspace_id)
    exhausted = BackgroundSearchError(
        "background_search_no_usable_image",
        "image search returned no usable approved background image",
    )

    # When / Then the operator sees the actionable Korean message and nothing moves
    with pytest.raises(CandidateImageStageError) as failure:
        _ = _runner(tmp_path, store, FakeBackgroundFetcher(failure=exhausted)).generate(
            workspace_id, candidate.candidate_id
        )
    assert "배경 이미지를 찾지 못했습니다" in failure.value.message
    assert "검색어를 바꾸거나" in failure.value.message
    unchanged = store.get_candidate(workspace_id, candidate.candidate_id)
    assert unchanged == candidate
    assert unchanged.status is CandidateStatus.CAPTION_APPROVED
    assert unchanged.image_path is None


def test_a_judgement_that_accepted_nothing_keeps_the_candidate_at_the_caption_gate(
    tmp_path: Path,
) -> None:
    # Given a judge that rejected every image both rounds collected
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = _approved_candidate(store, workspace_id)
    rejected = BackgroundSearchError(
        JUDGE_REJECTED_CODE,
        "background judge accepted none of the collected images",
    )

    # When / Then the reviewer is told to adjust the query, and no image is composed
    with pytest.raises(CandidateImageStageError) as failure:
        _ = _runner(tmp_path, store, FakeBackgroundFetcher(failure=rejected)).generate(
            workspace_id, candidate.candidate_id
        )
    assert "적합한 배경을 찾지 못했습니다" in failure.value.message
    assert "검색어를 조정해" in failure.value.message
    unchanged = store.get_candidate(workspace_id, candidate.candidate_id)
    assert unchanged.status is CandidateStatus.CAPTION_APPROVED
    assert unchanged.image_path is None


def test_an_exhausted_query_ladder_reaches_the_reviewer_with_its_diagnosis(
    tmp_path: Path,
) -> None:
    # Given a ladder that ran out of queries and said exactly why
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = _approved_candidate(store, workspace_id)
    diagnosis = (
        "적합한 배경을 찾지 못했습니다 — 검색 결과가 없었습니다. "
        "시도한 검색어: “쿠로미 배경화면 고화질” · “쿠로미 배경화면” — "
        "검색어를 조정해 다시 시도해 주세요."
    )
    exhausted = BackgroundSearchError(EXHAUSTED_CODE, diagnosis)

    # When / Then the diagnosis is shown verbatim rather than flattened to the generic line
    with pytest.raises(CandidateImageStageError) as failure:
        _ = _runner(tmp_path, store, FakeBackgroundFetcher(failure=exhausted)).generate(
            workspace_id, candidate.candidate_id
        )
    assert failure.value.message == diagnosis
    assert "시도한 검색어" in failure.value.message
    unchanged = store.get_candidate(workspace_id, candidate.candidate_id)
    assert unchanged.status is CandidateStatus.CAPTION_APPROVED
    assert unchanged.image_path is None


def test_a_background_that_cannot_be_written_is_reported_as_a_storage_failure(
    tmp_path: Path,
) -> None:
    # Given a search whose artifact write fails
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = _approved_candidate(store, workspace_id)
    write_failed = BackgroundSearchError(
        "background_artifact_write_failed",
        "searched background could not be written",
    )

    # When / Then the storage message is used instead of the search message
    with pytest.raises(CandidateImageStageError) as failure:
        _ = _runner(tmp_path, store, FakeBackgroundFetcher(failure=write_failed)).generate(
            workspace_id, candidate.candidate_id
        )
    assert "배경 이미지를 저장하지 못했습니다" in failure.value.message
    assert store.get_candidate(workspace_id, candidate.candidate_id) == candidate


def test_a_successful_run_records_the_background_provenance(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = _approved_candidate(store, workspace_id)

    # When
    composed = _runner(tmp_path, store, FakeBackgroundFetcher()).generate(
        workspace_id, candidate.candidate_id
    )

    # Then the searched source is written next to the background it produced
    job_root = tmp_path / "candidates" / composed.candidate_id / f"r{candidate.revision}"
    provenance = TypeAdapter(dict[str, str]).validate_json(
        (job_root / "inputs" / "background-source.json").read_bytes()
    )
    assert provenance["schema_version"] == "trace.background-search.v1"
    assert provenance["provider"] == "duckduckgo"
    assert provenance["source_url"] == "https://www.pexels.com/photo/night-desk-1/"
    assert (
        provenance["artifact_sha256"]
        == sha256((job_root / "inputs" / "background.png").read_bytes()).hexdigest()
    )


def test_rejecting_an_image_returns_the_candidate_for_a_new_one(tmp_path: Path) -> None:
    # Given a composed candidate awaiting image review
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = _approved_candidate(store, workspace_id)
    fetcher = FakeBackgroundFetcher()
    runner = _runner(tmp_path, store, fetcher)
    composed = runner.generate(workspace_id, candidate.candidate_id)

    # When the image is rejected and a new one is composed
    returned = store.review_candidate_image(
        workspace_id,
        composed.candidate_id,
        accepted=False,
        note="배경이 너무 어둡습니다",
        expected_revision=composed.revision,
    )
    recomposed = runner.generate(workspace_id, candidate.candidate_id)

    # Then the rejection is recorded and the new run produces its own image
    assert returned.status is CandidateStatus.CAPTION_APPROVED
    assert returned.review_note == "배경이 너무 어둡습니다"
    assert returned.image_path is None
    assert returned.image_sha256 is None
    assert recomposed.status is CandidateStatus.IMAGE_AWAITING_REVIEW
    assert recomposed.image_path is not None
    assert recomposed.image_path != composed.image_path
    assert len(fetcher.queries) == 2


def test_approving_an_image_submits_the_candidate(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = _approved_candidate(store, workspace_id)
    composed = _runner(tmp_path, store, FakeBackgroundFetcher()).generate(
        workspace_id, candidate.candidate_id
    )

    # When
    submitted = store.review_candidate_image(
        workspace_id,
        composed.candidate_id,
        accepted=True,
        note=None,
        expected_revision=composed.revision,
    )

    # Then
    assert submitted.status is CandidateStatus.SUBMITTED
    assert submitted.image_path == composed.image_path
    assert submitted.image_sha256 == composed.image_sha256


def test_the_packaged_assets_resolve_from_any_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a service started from a knowledge folder that holds no tool assets
    serve_root = tmp_path / "knowledge"
    serve_root.mkdir()
    monkeypatch.chdir(serve_root)
    monkeypatch.delenv(COMPONENT_FIXTURE_ENVIRONMENT, raising=False)
    monkeypatch.delenv(IPHONE_UI_ENVIRONMENT, raising=False)
    settings = AgentSettings.from_environment()
    store = SqliteWorkspaceStore(tmp_path / "workspace")

    # When the production runner is composed
    runner = build_local_candidate_image_runner(settings, tmp_path, store)

    # Then both local layers come from the installed package, not the working directory
    assert runner.options.component_fixture == default_trace_components_path()
    assert runner.options.iphone_ui_path == default_iphone_ui_path()
    assert runner.options.component_fixture.is_file()
    assert runner.options.iphone_ui_path.is_file()
    assert serve_root not in runner.options.component_fixture.parents


def test_an_asset_override_is_resolved_against_the_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given relative and absolute overrides for the two local layers
    serve_root = tmp_path / "knowledge"
    serve_root.mkdir()
    monkeypatch.chdir(serve_root)
    relative_fixture = _component_png(serve_root / "custom" / "components.png")
    absolute_ui = _ui_png(tmp_path / "elsewhere" / "iphone-ui.png")
    monkeypatch.setenv(COMPONENT_FIXTURE_ENVIRONMENT, "custom/components.png")
    monkeypatch.setenv(IPHONE_UI_ENVIRONMENT, str(absolute_ui))
    settings = AgentSettings.from_environment()
    store = SqliteWorkspaceStore(tmp_path / "workspace")

    # When the production runner is composed
    runner = build_local_candidate_image_runner(settings, tmp_path, store)

    # Then the relative override follows the working directory and the absolute one is kept
    assert runner.options.component_fixture == relative_fixture
    assert runner.options.iphone_ui_path == absolute_ui
