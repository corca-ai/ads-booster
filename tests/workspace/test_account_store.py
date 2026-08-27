from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.workspace import (
    CandidateBackgroundSubject,
    CandidatePersonaDomain,
    LockScreenFont,
    MarketingAccountCreate,
    MarketingAccountId,
    MarketingAccountIdentity,
    MarketingAccountSchedule,
    MarketingAccountSettings,
    MarketingAccountStatus,
    MarketingAccountTaste,
    RevisionConflictError,
    ScopedRecordNotFoundError,
    SqliteWorkspaceStore,
    WorkspaceId,
)

if TYPE_CHECKING:
    from pathlib import Path


def _identity(name: str = "박세나") -> MarketingAccountIdentity:
    return MarketingAccountIdentity(
        display_name=name,
        age=27,
        region="서울",
        occupation="병동 간호사",
        concept="3교대 근무를 잠금화면 일정으로 버티는 간호사",
        domain=CandidatePersonaDomain.OFFICE_WORKER,
        interests=("쿠로미", "필라테스"),
        voice="반말, 짧은 문장, 감탄사가 자주 붙는다",
        life_rhythm="데이 출근일 5시 40분 기상, 나이트 주간은 낮에 잔다",
        taste=MarketingAccountTaste(
            background_subject=CandidateBackgroundSubject.CHARACTER_OTHER,
            background_mood="파스텔 톤의 캐릭터 배경",
            font=LockScreenFont.SF_PRO_ROUNDED,
        ),
    )


def _schedule() -> MarketingAccountSchedule:
    return MarketingAccountSchedule(language="ko", timezone="Asia/Seoul")


def _store(tmp_path: Path) -> tuple[SqliteWorkspaceStore, WorkspaceId]:
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace")
    return store, workspace.workspace.workspace_id


def test_created_account_is_listed_with_its_identity(tmp_path: Path) -> None:
    store, workspace_id = _store(tmp_path)

    created = store.create_account(
        workspace_id,
        MarketingAccountCreate(country="KR", identity=_identity(), schedule=_schedule()),
    )

    listed = store.list_accounts(workspace_id)
    assert [record.account_id for record in listed] == [created.account_id]
    assert listed[0].identity.taste.font is LockScreenFont.SF_PRO_ROUNDED
    assert listed[0].identity.interests == ("쿠로미", "필라테스")
    assert listed[0].status is MarketingAccountStatus.OBSERVING


def test_status_change_is_the_human_verdict_and_bumps_the_revision(tmp_path: Path) -> None:
    store, workspace_id = _store(tmp_path)
    created = store.create_account(
        workspace_id,
        MarketingAccountCreate(country="KR", identity=_identity(), schedule=_schedule()),
    )

    promoted = store.set_account_status(
        workspace_id,
        created.account_id,
        status=MarketingAccountStatus.ACTIVE,
        expected_revision=created.revision,
    )

    assert promoted.status is MarketingAccountStatus.ACTIVE
    assert promoted.revision == created.revision + 1
    assert store.get_account(workspace_id, created.account_id).status is (
        MarketingAccountStatus.ACTIVE
    )


def test_a_stale_revision_is_refused_rather_than_overwriting(tmp_path: Path) -> None:
    store, workspace_id = _store(tmp_path)
    created = store.create_account(
        workspace_id,
        MarketingAccountCreate(country="KR", identity=_identity(), schedule=_schedule()),
    )
    _ = store.set_account_status(
        workspace_id,
        created.account_id,
        status=MarketingAccountStatus.ACTIVE,
        expected_revision=created.revision,
    )

    with pytest.raises(RevisionConflictError):
        _ = store.update_account(
            workspace_id,
            created.account_id,
            settings=MarketingAccountSettings(
                identity=_identity("다른 이름"),
                schedule=_schedule(),
            ),
            expected_revision=created.revision,
        )


def test_an_account_is_invisible_from_another_workspace(tmp_path: Path) -> None:
    store, workspace_id = _store(tmp_path)
    other = store.create_workspace("Other").workspace.workspace_id
    created = store.create_account(
        workspace_id,
        MarketingAccountCreate(country="KR", identity=_identity(), schedule=_schedule()),
    )

    assert store.list_accounts(other) == ()
    with pytest.raises(ScopedRecordNotFoundError):
        _ = store.get_account(other, created.account_id)


def test_an_unknown_account_is_not_found(tmp_path: Path) -> None:
    store, workspace_id = _store(tmp_path)

    with pytest.raises(ScopedRecordNotFoundError):
        _ = store.get_account(workspace_id, MarketingAccountId("missing"))
