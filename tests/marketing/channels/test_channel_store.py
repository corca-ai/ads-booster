from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ads_booster.marketing.channels.contracts import (
    ChannelInstallation,
    ChannelKind,
    ChannelNotification,
)
from ads_booster.marketing.channels.store import SqliteChannelStore

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def test_notification_outbox_is_durable_and_digest_bound(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    store = SqliteChannelStore(database)
    store.put_installation(
        ChannelInstallation(
            schema_version="trace.channel-installation.v1",
            installation_id="slack-installation",
            channel=ChannelKind.SLACK,
            external_workspace_id="team-one",
            tenant_id="trace",
            credential_reference="env:FAKE_SLACK_SECRET",
            created_at=NOW,
        )
    )
    notification = ChannelNotification(
        schema_version="trace.channel-notification.v1",
        notification_id="notification-one",
        installation_id="slack-installation",
        external_conversation_id="channel-one",
        run_id="run-one",
        kind="progress",
        payload={"state": "running"},
        created_at=NOW,
    )

    store.enqueue(notification)
    restarted = SqliteChannelStore(database)

    assert restarted.pending_notifications("slack-installation") == (notification,)
    restarted.enqueue(notification)
    with pytest.raises(ValueError, match="notification idempotency conflict"):
        restarted.enqueue(notification.model_copy(update={"payload": {"state": "failed"}}))
    restarted.mark_delivered(notification.notification_id, now=NOW)
    assert restarted.pending_notifications("slack-installation") == ()
    assert database.stat().st_mode & 0o777 == 0o600
