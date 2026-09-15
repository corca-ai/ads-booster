from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ads_booster.contracts.threads import (
    ThreadsAccount,
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
from ads_booster.threads.effect_fence import ThreadsEffectFence
from ads_booster.threads.media_delivery import ThreadsMediaDelivery
from ads_booster.threads.privacy import ThreadsPrivacyCallbacks
from ads_booster.threads.publications import ThreadsPublicationRepository
from tests.marketing.agent_service.threads_callback_fixtures import FAKE_APP_SECRET

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 9, 15, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ThreadsPrivacyPersistenceFixture:
    callbacks: ThreadsPrivacyCallbacks
    accounts: ThreadsAccountRepository
    tokens: ThreadsTokenVault
    drafts: ThreadsDraftRepository
    media: ThreadsMediaDelivery
    publications: ThreadsPublicationRepository

    def connect_draft(self, account: ThreadsAccount) -> ThreadsDraftBatch:
        _ = self.tokens.put(
            f"token-{account.connection_id}", token_ref=account.token_ref
        )
        _ = self.accounts.put(account)
        batch = threads_draft(account)
        _ = self.drafts.create(batch)
        return batch


def privacy_callbacks(
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
            ThreadsEffectFence(database_path),
        ),
        accounts,
        tokens,
    )


def privacy_persistence(root: Path) -> ThreadsPrivacyPersistenceFixture:
    callbacks, accounts, tokens = privacy_callbacks(root)
    drafts = ThreadsDraftRepository(callbacks.database_path)
    publications = ThreadsPublicationRepository(callbacks.database_path)
    _ = ProviderMetricRepository(callbacks.database_path)
    media = ThreadsMediaDelivery(
        callbacks.database_path,
        root / "artifacts",
        "https://agent.example.com",
        b"m" * 32,
    )
    return ThreadsPrivacyPersistenceFixture(
        callbacks, accounts, tokens, drafts, media, publications
    )


def threads_account(
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


def threads_draft(account: ThreadsAccount) -> ThreadsDraftBatch:
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


def publication_receipt(
    account: ThreadsAccount, batch: ThreadsDraftBatch
) -> ThreadsPublicationReceipt:
    return ThreadsPublicationReceipt(
        operation_id="operation-1",
        workspace_id=account.workspace_id,
        owner_member_id=account.owner_member_id,
        run_id="run-1",
        invocation_sha256="a" * 64,
        connection_id=account.connection_id,
        batch_id=batch.batch_id,
        item_id="item-1",
        draft_revision=batch.revision,
        ordered_asset_sha256=(),
        state="prepared",
        updated_at=NOW,
    )


__all__ = [
    "NOW",
    "ThreadsPrivacyPersistenceFixture",
    "privacy_callbacks",
    "privacy_persistence",
    "publication_receipt",
    "threads_account",
    "threads_draft",
]
