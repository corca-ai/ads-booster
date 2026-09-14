from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.threads import ThreadsAccount, ThreadsAccountStatus
from ads_booster.threads.accounts import (
    ThreadsAccountConflictError,
    ThreadsAccountRepository,
    ThreadsTokenVault,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_mark_reauth_preserves_account_identity(tmp_path: Path) -> None:
    # Given
    now = datetime(2026, 9, 15, tzinfo=UTC)
    repository = ThreadsAccountRepository(tmp_path / "agent.sqlite")
    account = repository.put(
        ThreadsAccount(
            connection_id="threads-connection",
            workspace_id="workspace",
            owner_member_id="member",
            provider_account_id="provider-account",
            username="trace",
            granted_scopes=("threads_basic",),
            token_ref=f"ref-{tmp_path.name}",
            connected_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=30),
        )
    )
    changed_at = now + timedelta(minutes=5)

    # When
    updated = repository.mark_reauth(account, now=changed_at)

    # Then
    assert updated == account.model_copy(
        update={"status": ThreadsAccountStatus.REAUTH_REQUIRED, "updated_at": changed_at}
    )


def test_token_put_rejects_invalid_reference(tmp_path: Path) -> None:
    # Given
    vault = ThreadsTokenVault(tmp_path / "tokens")
    invalid_reference = f"../{tmp_path.name}"

    # When / Then
    with pytest.raises(ThreadsAccountConflictError, match="threads_token_invalid"):
        vault.put("token", token_ref=invalid_reference)


def test_token_replace_rejects_invalid_reference(tmp_path: Path) -> None:
    # Given
    vault = ThreadsTokenVault(tmp_path / "tokens")
    invalid_reference = f"../{tmp_path.name}"

    # When / Then
    with pytest.raises(ThreadsAccountConflictError, match="threads_token_invalid"):
        vault.replace(invalid_reference, "token")
