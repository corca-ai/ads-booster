from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.contracts.provider_metrics import (
    ProviderMetricAvailability,
    ProviderMetricSnapshot,
)
from ads_booster.contracts.threads import (
    ThreadsAccount,
    ThreadsAccountStatus,
    ThreadsPublicationReceipt,
)
from ads_booster.learning.provider_metrics import ProviderMetricRepository
from ads_booster.threads.accounts import ThreadsAccountRepository, ThreadsTokenVault
from ads_booster.threads.drafts import (
    ThreadsDraftAction,
    ThreadsDraftBatch,
    ThreadsDraftItem,
    ThreadsDraftRepository,
)
from ads_booster.threads.media_delivery import ThreadsMediaDelivery
from ads_booster.threads.privacy import ThreadsPrivacyCallbacks
from ads_booster.threads.publications import ThreadsPublicationRepository
from tests.marketing.agent_service.threads_callback_fixtures import (
    FAKE_APP_SECRET,
    signed_request,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 9, 15, tzinfo=UTC)
_STRING_ROW: TypeAdapter[tuple[str]] = TypeAdapter(tuple[str])


def test_deauthorization_revokes_every_provider_connection_and_token(tmp_path: Path) -> None:
    # Given one provider user connected in two workspaces and one unrelated account.
    callbacks, accounts, tokens = _callbacks(tmp_path)
    owned = (_account("connection-1", "workspace-1"), _account("connection-2", "workspace-2"))
    unrelated = _account("connection-3", "workspace-3", provider_id="provider-user-2")
    for account in (*owned, unrelated):
        _ = tokens.put(f"token-{account.connection_id}", token_ref=account.token_ref)
        _ = accounts.put(account)

    # When Meta sends an authenticated app-removal callback.
    affected = callbacks.deauthorize(signed_request("provider-user-1"), now=NOW)

    # Then all matching connections are revoked and their credentials are gone.
    assert affected == 2
    for account in owned:
        revoked = accounts.get(account.workspace_id, account.connection_id)
        assert revoked is not None
        assert revoked.status is ThreadsAccountStatus.REVOKED
    assert all(not (tokens.root / account.token_ref).exists() for account in owned)
    assert accounts.get(unrelated.workspace_id, unrelated.connection_id) == unrelated
    assert (tokens.root / unrelated.token_ref).is_file()


def test_data_deletion_removes_provider_records_and_returns_stable_receipt(tmp_path: Path) -> None:
    # Given provider-derived data across every Threads persistence surface.
    callbacks, accounts, tokens = _callbacks(tmp_path)
    account = _account("connection-1", "workspace-1")
    _ = tokens.put("token-connection-1", token_ref=account.token_ref)
    _ = accounts.put(account)
    batch = _draft(account)
    _ = ThreadsDraftRepository(callbacks.database_path).create(batch)
    _ = ThreadsPublicationRepository(callbacks.database_path).put(
        ThreadsPublicationReceipt(
            operation_id="operation-1",
            workspace_id=account.workspace_id,
            owner_member_id=account.owner_member_id,
            run_id="run-1",
            invocation_sha256="a" * 64,
            connection_id=account.connection_id,
            batch_id=batch.batch_id,
            item_id="item-1",
            draft_revision=1,
            ordered_asset_sha256=(),
            state="prepared",
            updated_at=NOW,
        )
    )
    _ = ProviderMetricRepository(callbacks.database_path).append(
        ProviderMetricSnapshot(
            snapshot_id="snapshot-1",
            provider="threads",
            workspace_id=account.workspace_id,
            connection_id=account.connection_id,
            provider_account_id=account.provider_account_id,
            subject_kind="account",
            subject_id=account.provider_account_id,
            metric="views",
            period="lifetime",
            value=10,
            availability=ProviderMetricAvailability.AVAILABLE,
            observed_at=NOW,
            provider_api_version="v1.0",
            source_sha256="b" * 64,
        )
    )
    _seed_media_grant(callbacks.database_path, batch)

    # When Meta submits the same authenticated deletion request twice.
    signed_value = signed_request(account.provider_account_id)
    first = callbacks.delete(signed_value, now=NOW)
    second = callbacks.delete(signed_value, now=NOW + timedelta(minutes=1))

    # Then deletion is complete, idempotent, and retains no provider identity.
    assert first == second == callbacks.status(first.confirmation_code)
    assert first.status == "completed"
    assert not (tokens.root / account.token_ref).exists()
    with closing(sqlite3.connect(callbacks.database_path)) as database:
        counts = (
            database.execute("SELECT COUNT(*) FROM threads_accounts").fetchone()[0],
            database.execute("SELECT COUNT(*) FROM threads_draft_batches").fetchone()[0],
            database.execute("SELECT COUNT(*) FROM threads_draft_revisions").fetchone()[0],
            database.execute("SELECT COUNT(*) FROM threads_media_grants").fetchone()[0],
            database.execute("SELECT COUNT(*) FROM threads_publications").fetchone()[0],
            database.execute("SELECT COUNT(*) FROM threads_publication_events").fetchone()[0],
            database.execute("SELECT COUNT(*) FROM provider_metric_snapshots").fetchone()[0],
        )
        deletion_json = _STRING_ROW.validate_python(
            database.execute("SELECT receipt_json FROM threads_data_deletions").fetchone()
        )[0]
    assert counts == (0, 0, 0, 0, 0, 0, 0)
    assert account.provider_account_id not in deletion_json


def test_new_deletion_request_after_reconnect_deletes_new_account(tmp_path: Path) -> None:
    # Given a completed deletion followed by fresh consent for the same provider user.
    callbacks, accounts, tokens = _callbacks(tmp_path)
    _ = ThreadsDraftRepository(callbacks.database_path)
    _ = ThreadsPublicationRepository(callbacks.database_path)
    _ = ProviderMetricRepository(callbacks.database_path)
    account = _account("connection-1", "workspace-1")
    _ = tokens.put("old-token", token_ref=account.token_ref)
    _ = accounts.put(account)
    first = callbacks.delete(
        signed_request(account.provider_account_id, issued_at=1_789_416_000), now=NOW
    )
    _ = tokens.put("new-token", token_ref=account.token_ref)
    _ = accounts.put(account.model_copy(update={"updated_at": NOW + timedelta(minutes=1)}))

    # When Meta sends a later authenticated deletion request after the reconnection.
    second = callbacks.delete(
        signed_request(account.provider_account_id, issued_at=1_789_416_060),
        now=NOW + timedelta(minutes=2),
    )

    # Then the later request gets its own receipt and removes the new lifecycle.
    assert second.confirmation_code != first.confirmation_code
    assert accounts.get(account.workspace_id, account.connection_id) is None
    assert not (tokens.root / account.token_ref).exists()


def _callbacks(
    root: Path,
) -> tuple[ThreadsPrivacyCallbacks, ThreadsAccountRepository, ThreadsTokenVault]:
    database_path = root / "agent.sqlite3"
    accounts = ThreadsAccountRepository(database_path)
    tokens = ThreadsTokenVault(root / "secrets")
    return (
        ThreadsPrivacyCallbacks(
            database_path,
            "https://agent.example.com",
            FAKE_APP_SECRET,
            accounts,
            tokens,
        ),
        accounts,
        tokens,
    )


def _account(
    connection_id: str,
    workspace_id: str,
    *,
    provider_id: str = "provider-user-1",
) -> ThreadsAccount:
    return ThreadsAccount(
        connection_id=connection_id,
        workspace_id=workspace_id,
        owner_member_id=f"owner-{workspace_id}",
        provider_account_id=provider_id,
        username=f"user-{connection_id}",
        granted_scopes=("threads_basic",),
        token_ref=f"token-ref-{connection_id}",
        connected_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=60),
    )


def _draft(account: ThreadsAccount) -> ThreadsDraftBatch:
    return ThreadsDraftBatch(
        batch_id="batch-1",
        workspace_id=account.workspace_id,
        owner_member_id=account.owner_member_id,
        conversation_id="conversation-1",
        source_event_id="event-1",
        items=(
            ThreadsDraftItem(
                item_id="item-1",
                action=ThreadsDraftAction.PUBLISH,
                connection_id=account.connection_id,
                text="Delete this provider draft",
            ),
        ),
        created_at=NOW,
        updated_at=NOW,
    )


def _seed_media_grant(database_path: Path, batch: ThreadsDraftBatch) -> None:
    _ = ThreadsMediaDelivery(
        database_path,
        database_path.parent / "artifacts",
        "https://agent.example.com",
        b"m" * 32,
    )
    with closing(sqlite3.connect(database_path)) as database, database:
        _ = database.execute(
            """INSERT INTO threads_media_grants VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                "token-sha",
                batch.workspace_id,
                batch.batch_id,
                batch.revision,
                "item-1",
                "asset-1",
                1,
                "c" * 64,
                (NOW + timedelta(minutes=5)).isoformat(),
            ),
        )
