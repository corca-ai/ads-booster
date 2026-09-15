from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import TYPE_CHECKING

import httpx2
import pytest

from ads_booster.learning.provider_metrics import ProviderMetricRepository
from ads_booster.providers.threads_api import ThreadsApiClient
from ads_booster.threads.drafts import ThreadsDraftAction, ThreadsDraftRepository
from ads_booster.threads.media_delivery import ThreadsMediaDelivery
from ads_booster.threads.publications import (
    ThreadsPublicationError,
    ThreadsPublicationRepository,
    ThreadsPublisher,
)
from tests.marketing.agent_service.threads_callback_fixtures import (
    FAKE_APP_SECRET,
    signed_request,
)
from tests.marketing.agent_service.threads_privacy_fixtures import (
    NOW,
    privacy_callbacks,
    publication_receipt,
    threads_account,
    threads_draft,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.threads.privacy import ThreadsDeletionReceipt


def test_completed_deletion_rejects_stale_publication_write(tmp_path: Path) -> None:
    # Given a prepared publication that belongs to a provider deletion request.
    callbacks, accounts, tokens = privacy_callbacks(tmp_path)
    drafts = ThreadsDraftRepository(callbacks.database_path)
    publications = ThreadsPublicationRepository(callbacks.database_path)
    _ = ProviderMetricRepository(callbacks.database_path)
    _ = ThreadsMediaDelivery(
        callbacks.database_path,
        tmp_path / "artifacts",
        "https://agent.example.com",
        b"m" * 32,
    )
    account = threads_account("connection-1", "workspace-1")
    _ = tokens.put("token", token_ref=account.token_ref)
    _ = accounts.put(account)
    batch = threads_draft(account)
    _ = drafts.create(batch)
    prepared = publications.put(publication_receipt(account, batch))
    _ = callbacks.delete(signed_request(account.provider_account_id), now=NOW)

    # When an old worker tries to recreate the deleted publication ledger.
    with pytest.raises(ThreadsPublicationError, match="threads_connection_deleted"):
        _ = publications.put(
            prepared.model_copy(update={"state": "publishing", "updated_at": NOW})
        )

    # Then the completed deletion remains authoritative.
    assert publications.get(prepared.operation_id) is None


def test_data_deletion_waits_for_in_flight_publication(tmp_path: Path) -> None:
    # Given a publication blocked inside the provider publish request.
    callbacks, accounts, tokens = privacy_callbacks(tmp_path)
    drafts = ThreadsDraftRepository(callbacks.database_path)
    publications = ThreadsPublicationRepository(callbacks.database_path)
    _ = ProviderMetricRepository(callbacks.database_path)
    media = ThreadsMediaDelivery(
        callbacks.database_path,
        tmp_path / "artifacts",
        "https://agent.example.com",
        b"m" * 32,
    )
    account = threads_account("connection-1", "workspace-1").model_copy(
        update={"granted_scopes": ("threads_basic", "threads_content_publish")}
    )
    _ = tokens.put("token", token_ref=account.token_ref)
    _ = accounts.put(account)
    batch = threads_draft(account)
    _ = drafts.create(batch)
    publish_started = Event()
    publish_release = Event()
    deletion_completed = Event()

    def route(request: httpx2.Request) -> httpx2.Response:
        key = (request.method, request.url.path)
        if key == ("POST", "/v1.0/me/threads_publish"):
            publish_started.set()
            assert publish_release.wait(timeout=5)
        responses = {
            ("POST", "/v1.0/me/threads"): {"id": "creation-1"},
            ("GET", "/v1.0/creation-1"): {
                "id": "creation-1",
                "status": "FINISHED",
            },
            ("POST", "/v1.0/me/threads_publish"): {"id": "post-1"},
            ("GET", "/v1.0/post-1"): {
                "id": "post-1",
                "username": "trace",
                "permalink": "https://threads.net/post-1",
                "timestamp": NOW.isoformat(),
            },
        }
        payload = responses.get(key)
        if payload is None:
            message = f"unexpected provider request: {key}"
            raise AssertionError(message)
        return httpx2.Response(200, json=payload)

    def delete_provider_data() -> ThreadsDeletionReceipt:
        try:
            return callbacks.delete(signed_request(account.provider_account_id), now=NOW)
        finally:
            deletion_completed.set()

    with (
        httpx2.Client(
            base_url="https://graph.threads.net", transport=httpx2.MockTransport(route)
        ) as client,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        publisher = ThreadsPublisher(
            ThreadsApiClient("app-id", FAKE_APP_SECRET, client, sleeper=lambda _delay: None),
            accounts,
            tokens,
            drafts,
            media,
            publications,
            callbacks.effect_fence,
        )
        # When publication and deletion run concurrently.
        publish_future = executor.submit(
            publisher.publish,
            operation_id="operation-race",
            workspace_id=account.workspace_id,
            member_id=account.owner_member_id,
            batch_id=batch.batch_id,
            batch_revision=batch.revision,
            item_id="item-1",
            expected_action=ThreadsDraftAction.PUBLISH,
            run_id="run-1",
            invocation_sha256="a" * 64,
            now=NOW,
        )
        assert publish_started.wait(timeout=2)
        deletion_future = executor.submit(delete_provider_data)
        try:
            # Then deletion cannot complete while the provider write is unresolved.
            assert not deletion_completed.wait(timeout=0.2)
        finally:
            publish_release.set()

        assert publish_future.result(timeout=5).state == "published"
        assert deletion_future.result(timeout=5).status == "completed"

    assert publications.get("operation-race") is None
