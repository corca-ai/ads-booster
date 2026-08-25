from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from PIL import Image
from pydantic import TypeAdapter

from trace_capture.candidate_generation import (
    CandidateImageOptions,
    CandidateImageRunner,
    CandidateImageStageError,
    build_background_query,
)
from trace_capture.search.image.background import BackgroundSearchError, SearchedBackground
from trace_capture.workspace import (
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

    from trace_capture.candidate_generation import CandidateBackgroundPort
    from trace_capture.workspace import CandidateRecord, WorkspaceId


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
    """Stands in for the shared image-search fetcher, recording the queries it receives."""

    failure: Exception | None = None
    queries: list[str] = field(default_factory=list)

    def fetch(self, query: str, destination: Path) -> SearchedBackground:
        self.queries.append(query)
        if self.failure is not None:
            raise self.failure
        _ = _background_png(destination)
        return SearchedBackground(
            path=destination,
            sha256=sha256(destination.read_bytes()).hexdigest(),
            query=query,
            provider="duckduckgo",
            image_url="https://images.pexels.com/photos/1/night-desk.jpeg",
            source_url="https://www.pexels.com/photo/night-desk-1/",
        )


@dataclass(frozen=True, slots=True)
class FakeBackgroundSource:
    fetcher: FakeBackgroundFetcher

    @contextmanager
    def open(self) -> Generator[CandidateBackgroundPort]:
        yield self.fetcher


def _image_inputs() -> CandidateImageInputs:
    return CandidateImageInputs(
        trace_items=("09:00 통계학 2교시", "13:00 스터디", "19:00 러닝"),
        device_time="07:20",
        background_subject=CandidateBackgroundSubject.SCENERY,
        background_mood="늦은 밤 책상 위 스탠드 불빛",
        language="ko",
    )


def _approved_candidate(store: SqliteWorkspaceStore, workspace_id: WorkspaceId) -> CandidateRecord:
    created = store.create_candidate(
        CandidateCreate(
            workspace_id=workspace_id,
            source=CandidateSource.AUTO,
            country="KR",
            topic="시험기간 일정 관리",
            caption="시험 기간엔 잠금화면부터 바꾼다",
            hypothesis="1인칭 감탄이 저장률을 올린다",
            image_inputs=_image_inputs(),
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
) -> CandidateImageRunner:
    return CandidateImageRunner(
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


def test_background_query_uses_the_topic_and_image_inputs(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    candidate = _approved_candidate(store, _workspace(store))

    # When
    query = build_background_query(candidate)

    # Then
    assert "natural scenery landscape photo" in query
    assert "늦은 밤 책상 위 스탠드 불빛" in query
    assert "시험기간 일정 관리" in query
    assert "KR" in query
    assert "no text no logo no phone no UI" in query


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
    assert "BRAVE_SEARCH_API_KEY" in failure.value.message
    unchanged = store.get_candidate(workspace_id, candidate.candidate_id)
    assert unchanged == candidate
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
