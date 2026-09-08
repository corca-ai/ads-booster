from __future__ import annotations

import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TYPE_CHECKING, ClassVar

import pytest
from pydantic import TypeAdapter

from ads_booster.knowledge.contracts import AttachmentCapability
from ads_booster.knowledge.ingest_receipts import IngestDeliveryReceipt, IngestUnitKind
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.repository import SqliteKnowledgeRepository
from ads_booster.knowledge.repository_types import MembershipRole
from ads_booster.knowledge.source_fetch import SourceFetchRequest
from ads_booster.agent.service.knowledge_ingress import TrustedRunBinding
from ads_booster.channels.slack_attachments import (
    SlackAttachmentFetcher,
    SlackAttachmentFetchError,
    SlackScopedSourceFetcher,
)
from ads_booster.channels.slack_events import SlackEvents
from tests.marketing.agent_service.test_http_api import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path

_ADDRESS: TypeAdapter[tuple[str, int]] = TypeAdapter(tuple[str, int])
_INTEGER_ROW: TypeAdapter[tuple[int]] = TypeAdapter(tuple[int])
_OUTBOX_ROWS: TypeAdapter[list[tuple[str, str, int]]] = TypeAdapter(list[tuple[str, str, int]])
_TEXT_ROWS: TypeAdapter[list[tuple[str, ...]]] = TypeAdapter(list[tuple[str, ...]])


class AttachmentHandler(BaseHTTPRequestHandler):
    paths: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        self.paths.append(self.path)
        if self.headers.get("Authorization") != "Bearer synthetic-token":
            self.send_response(403)
            self.end_headers()
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
            return
        body = b"too-large" if self.path == "/large" else b"attachment"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        _ = self.wfile.write(body)


def test_slack_attachment_fetch_is_bounded_and_rejects_redirect() -> None:
    # Given: a loopback Slack-compatible origin and a scoped synthetic capability.
    server = ThreadingHTTPServer(("127.0.0.1", 0), AttachmentHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = _ADDRESS.validate_python(server.server_address)
    fetcher = SlackAttachmentFetcher(
        "synthetic-token",
        allowed_hosts=frozenset({str(host)}),
        allowed_schemes=frozenset({"http"}),
        max_bytes=16,
    )
    base = f"http://{host}:{port}"

    try:
        # When: the adapter fetches a valid file and probes redirect and size failures.
        fetched = fetcher.fetch(_attachment(base + "/ok"))
        scoped = SlackScopedSourceFetcher(fetcher).fetch(
            SourceFetchRequest(url=base + "/ok?query-canary#fragment-canary")
        )
        with pytest.raises(SlackAttachmentFetchError, match="redirect_rejected"):
            _ = fetcher.fetch(_attachment(base + "/redirect"))
        with pytest.raises(SlackAttachmentFetchError, match="too_large"):
            _ = SlackAttachmentFetcher(
                "synthetic-token",
                allowed_hosts=frozenset({str(host)}),
                allowed_schemes=frozenset({"http"}),
                max_bytes=4,
            ).fetch(_attachment(base + "/large"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    # Then: only the authorized direct response bytes are returned.
    assert fetched.body == b"attachment"
    assert scoped.original_url == base + "/ok"
    assert scoped.final_url == base + "/ok"


def test_signed_multi_attachment_event_commits_each_scoped_source(tmp_path: Path) -> None:
    # Given: a signed multi-attachment Slack event and a credential-owning loopback adapter.
    server = ThreadingHTTPServer(("127.0.0.1", 0), AttachmentHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = _ADDRESS.validate_python(server.server_address)
    owner, _ = setup_events(tmp_path)
    receive(
        owner,
        subtype="file_share",
        text="<@UBOT>",
        files=[
            {
                "id": "F1",
                "mimetype": "text/plain",
                "url_private_download": (f"http://{host}:{port}/ok?query-canary#fragment-canary"),
            },
            {
                "id": "F2",
                "mimetype": "text/plain",
                "url_private_download": f"http://{host}:{port}/ok",
            },
        ],
    )
    with closing(sqlite3.connect(tmp_path / "agent.sqlite3")) as db:
        binding_row = _TEXT_ROWS.validate_python(
            db.execute("SELECT binding_json FROM knowledge_run_bindings").fetchall()
        )
    binding = TrustedRunBinding.model_validate_json(binding_row[0][0])
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge-root")
    repository.register_actor(binding.actor, MembershipRole.EDITOR)
    fetcher = SlackScopedSourceFetcher(
        SlackAttachmentFetcher(
            "synthetic-token",
            allowed_hosts=frozenset({host}),
            allowed_schemes=frozenset({"http"}),
        )
    )
    restarted = SlackEvents(
        owner.commands,
        "UBOT",
        frozenset({"C1"}),
        knowledge_sink=KnowledgeIngestion(repository, url_fetcher=fetcher),
    )

    try:
        # When: the Slack worker delivers both attachment capabilities to the real sink.
        assert restarted.work_once(now=NOW)
        assert restarted.work_once(now=NOW)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    # Then: both knowledge sources exist without exposing the token.
    with repository.connection() as db:
        source_row = _TEXT_ROWS.validate_python(
            db.execute("SELECT source_json FROM sources").fetchall()
        )
        observation_rows = _TEXT_ROWS.validate_python(
            db.execute("SELECT final_url FROM source_observations").fetchall()
        )
    with closing(sqlite3.connect(tmp_path / "agent.sqlite3")) as db:
        outbox_row = _TEXT_ROWS.validate_python(
            db.execute("SELECT state,receipt_json FROM knowledge_ingress_outbox").fetchall()
        )
    delivery_receipt = IngestDeliveryReceipt.model_validate_json(outbox_row[0][1])
    assert len(source_row) == 2
    assert len(outbox_row) == 1
    assert outbox_row[0][0] == "acked"
    assert tuple((unit.kind, unit.ordinal) for unit in delivery_receipt.unit_receipts) == (
        (IngestUnitKind.ATTACHMENT, 0),
        (IngestUnitKind.ATTACHMENT, 1),
    )
    assert all("synthetic-token" not in row[0] for row in source_row)
    assert all("query-canary" not in row[0] for row in source_row)
    assert all("fragment-canary" not in row[0] for row in source_row)
    assert all("query-canary" not in row[0] for row in observation_rows)
    assert all("fragment-canary" not in row[0] for row in observation_rows)


def test_signed_slack_attachment_rejects_url_userinfo_before_network_or_storage(
    tmp_path: Path,
) -> None:
    # Given: a signed Slack file capability embeds RFC userinfo on an otherwise allowed host.
    AttachmentHandler.paths = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), AttachmentHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = _ADDRESS.validate_python(server.server_address)
    owner, messages = setup_events(tmp_path)
    receive(
        owner,
        subtype="file_share",
        text="<@UBOT>",
        files=[
            {
                "id": "F1",
                "mimetype": "text/plain",
                "url_private_download": f"http://user:pass@{host}:{port}/ok?auth=secret",
            }
        ],
    )
    with closing(sqlite3.connect(tmp_path / "agent.sqlite3")) as db:
        binding_row = _TEXT_ROWS.validate_python(
            db.execute("SELECT binding_json FROM knowledge_run_bindings").fetchall()
        )
    binding = TrustedRunBinding.model_validate_json(binding_row[0][0])
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge-root")
    repository.register_actor(binding.actor, MembershipRole.EDITOR)
    restarted = SlackEvents(
        owner.commands,
        "UBOT",
        frozenset({"C1"}),
        knowledge_sink=KnowledgeIngestion(
            repository,
            url_fetcher=SlackScopedSourceFetcher(
                SlackAttachmentFetcher(
                    "synthetic-token",
                    allowed_hosts=frozenset({host}),
                    allowed_schemes=frozenset({"http"}),
                )
            ),
        ),
    )

    try:
        # When: the canonical outbox dispatches the untrusted capability.
        assert restarted.work_once(now=NOW)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    # Then: rejection precedes the HTTP request and canonical knowledge writes.
    with repository.connection() as db:
        source_count = _INTEGER_ROW.validate_python(
            db.execute("SELECT COUNT(*) FROM sources").fetchone()
        )[0]
        observation_count = _INTEGER_ROW.validate_python(
            db.execute("SELECT COUNT(*) FROM source_observations").fetchone()
        )[0]
    with closing(sqlite3.connect(tmp_path / "agent.sqlite3")) as db:
        failed_outbox = _OUTBOX_ROWS.validate_python(
            db.execute("SELECT state,error_code,attempts FROM knowledge_ingress_outbox").fetchall()
        )
        job_states = _TEXT_ROWS.validate_python(
            db.execute("SELECT state FROM slack_message_jobs").fetchall()
        )
    assert AttachmentHandler.paths == []
    assert source_count == 0
    assert observation_count == 0
    assert failed_outbox == [("failed", "slack_attachment_origin_rejected", 1)]
    assert job_states == [("pending",)]
    assert not restarted.work_once(now=NOW)
    assert messages == []


def _attachment(url: str) -> AttachmentCapability:
    return AttachmentCapability(
        ordinal=0,
        logical_source_ref="source-one",
        logical_revision_ref="revision-one",
        mime_type="text/plain",
        capability_ref="file-one",
        source_url=url,
    )
