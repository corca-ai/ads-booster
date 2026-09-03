"""Durable channel installations, delivery dedupe, and notification outbox."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from pydantic import TypeAdapter

from ads_booster.contracts.canonical import canonical_sha256
from ads_booster.marketing.channels.contracts import (
    ChannelIdentityBinding,
    ChannelInstallation,
    ChannelKind,
    ChannelNotification,
    ChannelResponse,
)
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Generator
    from datetime import datetime
    from pathlib import Path

_MAX_OUTBOX_PAGE = 1000
_STRING = TypeAdapter(str)


@dataclass(frozen=True, slots=True)
class DeliveryAdmission:
    state: Literal["new", "pending", "completed"]
    response: ChannelResponse | None = None


@dataclass(slots=True)
class SqliteChannelStore:
    database_path: Path

    def __post_init__(self) -> None:
        """Create the additive channel tables and protect the local database."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            _ = connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS channel_installations (
                    installation_id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    external_workspace_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    installation_json TEXT NOT NULL,
                    UNIQUE(channel, external_workspace_id)
                );
                CREATE TABLE IF NOT EXISTS channel_identity_bindings (
                    binding_id TEXT PRIMARY KEY,
                    installation_id TEXT NOT NULL,
                    external_user_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    binding_json TEXT NOT NULL,
                    UNIQUE(installation_id, external_user_id),
                    FOREIGN KEY(installation_id) REFERENCES channel_installations(installation_id)
                );
                CREATE TABLE IF NOT EXISTS channel_deliveries (
                    installation_id TEXT NOT NULL,
                    delivery_id TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'completed')),
                    response_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(installation_id, delivery_id),
                    FOREIGN KEY(installation_id) REFERENCES channel_installations(installation_id)
                );
                CREATE TABLE IF NOT EXISTS channel_notification_outbox (
                    notification_id TEXT PRIMARY KEY,
                    installation_id TEXT NOT NULL,
                    notification_sha256 TEXT NOT NULL,
                    notification_json TEXT NOT NULL,
                    delivered_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(installation_id) REFERENCES channel_installations(installation_id)
                );
                CREATE INDEX IF NOT EXISTS channel_notification_pending
                ON channel_notification_outbox(installation_id, delivered_at, created_at);
                """
            )
        self.database_path.chmod(0o600)

    def put_installation(self, installation: ChannelInstallation) -> None:
        payload = installation.model_dump_json()
        try:
            with self._connection() as connection:
                _ = connection.execute(
                    """
                    INSERT INTO channel_installations(
                        installation_id, channel, external_workspace_id,
                        tenant_id, installation_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        installation.installation_id,
                        installation.channel.value,
                        installation.external_workspace_id,
                        installation.tenant_id,
                        payload,
                    ),
                )
        except sqlite3.IntegrityError as error:
            current = self.installation(installation.installation_id)
            if current == installation:
                return
            raise ValueError("channel installation identity conflict") from error

    def put_identity(self, binding: ChannelIdentityBinding) -> None:
        installation = self.installation(binding.installation_id)
        if installation is None or installation.tenant_id != binding.tenant_id:
            raise ValueError("channel identity installation mismatch")
        try:
            with self._connection() as connection:
                _ = connection.execute(
                    """
                    INSERT INTO channel_identity_bindings(
                        binding_id, installation_id, external_user_id, tenant_id, binding_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        binding.binding_id,
                        binding.installation_id,
                        binding.external_user_id,
                        binding.tenant_id,
                        binding.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            current = self._identity_by_binding_id(binding.binding_id)
            if current == binding:
                return
            raise ValueError("channel identity binding conflict") from error

    def installation(self, installation_id: str) -> ChannelInstallation | None:
        with self._connection() as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    "SELECT installation_json FROM channel_installations WHERE installation_id = ?",
                    (installation_id,),
                ).fetchone(),
            )
        return (
            None
            if row is None
            else ChannelInstallation.model_validate_json(_STRING.validate_python(row[0]))
        )

    def resolve_installation(
        self,
        channel: ChannelKind,
        external_workspace_id: str,
    ) -> ChannelInstallation:
        with self._connection() as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """
                    SELECT installation_json FROM channel_installations
                    WHERE channel = ? AND external_workspace_id = ?
                    """,
                    (channel.value, external_workspace_id),
                ).fetchone(),
            )
        if row is None:
            raise ValueError("channel installation not found")
        installation = ChannelInstallation.model_validate_json(_STRING.validate_python(row[0]))
        if not installation.enabled:
            raise ValueError("channel installation disabled")
        return installation

    def resolve_identity(
        self,
        installation_id: str,
        external_user_id: str,
    ) -> ChannelIdentityBinding:
        with self._connection() as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """
                    SELECT binding_json FROM channel_identity_bindings
                    WHERE installation_id = ? AND external_user_id = ?
                    """,
                    (installation_id, external_user_id),
                ).fetchone(),
            )
        if row is None:
            raise ValueError("channel identity not bound")
        binding = ChannelIdentityBinding.model_validate_json(_STRING.validate_python(row[0]))
        if binding.revoked_at is not None:
            raise ValueError("channel identity revoked")
        return binding

    def admit_delivery(
        self,
        installation_id: str,
        delivery_id: str,
        request: JsonObject,
        *,
        now: datetime,
    ) -> DeliveryAdmission:
        request_sha256 = canonical_sha256(request)
        with self._connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """
                    SELECT request_sha256, status, response_json FROM channel_deliveries
                    WHERE installation_id = ? AND delivery_id = ?
                    """,
                    (installation_id, delivery_id),
                ).fetchone(),
            )
            if row is not None:
                if _STRING.validate_python(row[0]) != request_sha256:
                    raise ValueError("channel delivery idempotency conflict")
                if _STRING.validate_python(row[1]) == "completed":
                    response = ChannelResponse.model_validate_json(_STRING.validate_python(row[2]))
                    return DeliveryAdmission("completed", response)
                return DeliveryAdmission("pending")
            _ = connection.execute(
                """
                INSERT INTO channel_deliveries(
                    installation_id, delivery_id, request_sha256, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (installation_id, delivery_id, request_sha256, now.isoformat(), now.isoformat()),
            )
        return DeliveryAdmission("new")

    def complete_delivery(
        self,
        installation_id: str,
        delivery_id: str,
        response: ChannelResponse,
        *,
        now: datetime,
        notification: ChannelNotification | None = None,
    ) -> None:
        with self._connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE channel_deliveries
                SET status = 'completed', response_json = ?, updated_at = ?
                WHERE installation_id = ? AND delivery_id = ? AND status = 'pending'
                """,
                (response.model_dump_json(), now.isoformat(), installation_id, delivery_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("channel delivery completion rejected")
            if notification is not None:
                payload = notification.model_dump(mode="json")
                _ = connection.execute(
                    """
                    INSERT INTO channel_notification_outbox(
                        notification_id, installation_id, notification_sha256,
                        notification_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        notification.notification_id,
                        notification.installation_id,
                        canonical_sha256(payload),
                        json.dumps(
                            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                        ),
                        notification.created_at.isoformat(),
                    ),
                )

    def enqueue(self, notification: ChannelNotification) -> None:
        payload = notification.model_dump(mode="json")
        digest = canonical_sha256(payload)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        try:
            with self._connection() as connection:
                _ = connection.execute(
                    """
                    INSERT INTO channel_notification_outbox(
                        notification_id, installation_id, notification_sha256,
                        notification_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        notification.notification_id,
                        notification.installation_id,
                        digest,
                        encoded,
                        notification.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            existing = self._notification_digest(notification.notification_id)
            if existing != digest:
                raise ValueError("channel notification idempotency conflict") from error

    def pending_notifications(
        self,
        installation_id: str,
        *,
        limit: int = 100,
    ) -> tuple[ChannelNotification, ...]:
        if limit < 1 or limit > _MAX_OUTBOX_PAGE:
            raise ValueError("channel notification limit invalid")
        with self._connection() as connection:
            rows = cast(
                "list[tuple[object, ...]]",
                connection.execute(
                    """
                    SELECT notification_json FROM channel_notification_outbox
                    WHERE installation_id = ? AND delivered_at IS NULL
                    ORDER BY created_at, notification_id LIMIT ?
                    """,
                    (installation_id, limit),
                ).fetchall(),
            )
        return tuple(
            ChannelNotification.model_validate_json(_STRING.validate_python(row[0])) for row in rows
        )

    def mark_delivered(self, notification_id: str, *, now: datetime) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE channel_notification_outbox SET delivered_at = ?
                WHERE notification_id = ? AND delivered_at IS NULL
                """,
                (now.isoformat(), notification_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("channel notification delivery rejected")

    def _notification_digest(self, notification_id: str) -> str | None:
        with self._connection() as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """
                    SELECT notification_sha256 FROM channel_notification_outbox
                    WHERE notification_id = ?
                    """,
                    (notification_id,),
                ).fetchone(),
            )
        return None if row is None else _STRING.validate_python(row[0])

    def _identity_by_binding_id(self, binding_id: str) -> ChannelIdentityBinding | None:
        with self._connection() as connection:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    """
                    SELECT binding_json FROM channel_identity_bindings WHERE binding_id = ?
                    """,
                    (binding_id,),
                ).fetchone(),
            )
        return (
            None
            if row is None
            else ChannelIdentityBinding.model_validate_json(_STRING.validate_python(row[0]))
        )

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, isolation_level=None)
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            if connection.in_transaction:
                connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()


__all__ = ["DeliveryAdmission", "SqliteChannelStore"]
