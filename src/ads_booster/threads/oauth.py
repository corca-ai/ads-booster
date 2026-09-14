from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from urllib.parse import urlencode

from pydantic import TypeAdapter

from ads_booster.contracts.threads import ThreadsAccount, ThreadsAccountStatus
from ads_booster.providers.threads_api import ThreadsApiClient, ThreadsApiError, ThreadsToken
from ads_booster.threads.accounts import (
    ThreadsAccountConflictError,
    ThreadsAccountRepository,
    ThreadsTokenVault,
)


class ThreadsOAuthError(ValueError):
    pass


_STATE_ROW: TypeAdapter[tuple[str, str, str, str, str, int] | None] = TypeAdapter(
    tuple[str, str, str, str, str, int] | None
)


@dataclass(frozen=True, slots=True)
class ThreadsConnectLink:
    state_id: str
    authorization_url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ThreadsOAuthService:
    database_path: str
    redirect_uri: str
    api: ThreadsApiClient
    accounts: ThreadsAccountRepository
    tokens: ThreadsTokenVault

    def __post_init__(self) -> None:
        with sqlite3.connect(self.database_path) as database:
            _ = database.executescript(
                """
                CREATE TABLE IF NOT EXISTS threads_oauth_states (
                    state_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    member_id TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    requested_scopes TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0
                );
                """
            )

    def start(
        self,
        *,
        workspace_id: str,
        member_id: str,
        scopes: tuple[str, ...],
        now: datetime,
    ) -> ThreadsConnectLink:
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ThreadsOAuthError("threads_oauth_time_requires_utc")
        state_id = token_urlsafe(32)
        expires_at = now + timedelta(minutes=10)
        normalized_scopes = tuple(sorted(set(scopes)))
        if not normalized_scopes or "threads_basic" not in normalized_scopes:
            raise ThreadsOAuthError("threads_basic_scope_required")
        with sqlite3.connect(self.database_path) as database:
            _ = database.execute(
                """INSERT INTO threads_oauth_states(
                state_id,workspace_id,member_id,redirect_uri,requested_scopes,expires_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    state_id,
                    workspace_id,
                    member_id,
                    self.redirect_uri,
                    ",".join(normalized_scopes),
                    expires_at.isoformat(),
                ),
            )
        query = urlencode(
            {
                "client_id": self.api.app_id,
                "redirect_uri": self.redirect_uri,
                "scope": ",".join(normalized_scopes),
                "response_type": "code",
                "state": state_id,
            }
        )
        return ThreadsConnectLink(
            state_id,
            f"https://threads.net/oauth/authorize?{query}",
            expires_at,
        )

    def finish(self, *, state_id: str, code: str, now: datetime) -> ThreadsAccount:
        workspace_id, member_id, requested = self._consume(state_id, now=now)
        short = self.api.exchange_code(code=code, redirect_uri=self.redirect_uri, now=now)
        token = self.api.exchange_long_lived(short, now=now)
        user = self.api.user(token.access_token)
        granted = self.api.granted_scopes(token.access_token)
        if not set(requested).issubset(granted):
            raise ThreadsOAuthError("threads_requested_scope_missing")
        connection_id = (
            "threads-" + sha256(f"{workspace_id}\n{member_id}\n{user.id}".encode()).hexdigest()[:24]
        )
        existing = next(
            (
                account
                for account in self.accounts.list_for_workspace(workspace_id)
                if account.provider_account_id == user.id
            ),
            None,
        )
        if existing is not None and existing.owner_member_id != member_id:
            raise ThreadsAccountConflictError("threads_provider_account_already_connected")
        if existing is not None:
            self.tokens.replace(existing.token_ref, token.access_token)
            return self.accounts.put(
                existing.model_copy(
                    update={
                        "username": user.username,
                        "granted_scopes": tuple(sorted(granted)),
                        "status": ThreadsAccountStatus.ACTIVE,
                        "updated_at": now,
                        "expires_at": token.expires_at,
                    }
                )
            )
        token_ref = connection_id
        try:
            _ = self.tokens.put(token.access_token, token_ref=token_ref)
        except FileExistsError:
            self.tokens.replace(token_ref, token.access_token)
        account = ThreadsAccount(
            connection_id=connection_id,
            workspace_id=workspace_id,
            owner_member_id=member_id,
            provider_account_id=user.id,
            username=user.username,
            granted_scopes=tuple(sorted(granted)),
            token_ref=token_ref,
            connected_at=now,
            updated_at=now,
            expires_at=token.expires_at,
        )
        try:
            return self.accounts.put(account)
        except ThreadsAccountConflictError:
            self.tokens.delete(token_ref)
            raise

    def refresh_account(
        self, workspace_id: str, connection_id: str, member_id: str, *, now: datetime
    ) -> ThreadsAccount:
        account = self.accounts.require_owned(workspace_id, connection_id, member_id)
        if account.status is ThreadsAccountStatus.REVOKED:
            raise ThreadsOAuthError("threads_account_reconnect_required")
        try:
            refreshed = self.api.refresh(
                ThreadsToken(
                    self.tokens.get(account.token_ref),
                    account.provider_account_id,
                    account.expires_at,
                ),
                now=now,
            )
        except ThreadsApiError as error:
            if error.status == 401:
                _ = self.accounts.mark_reauth(account, now=now)
            raise
        self.tokens.replace(account.token_ref, refreshed.access_token)
        return self.accounts.put(
            account.model_copy(
                update={
                    "status": ThreadsAccountStatus.ACTIVE,
                    "updated_at": now,
                    "expires_at": refreshed.expires_at,
                }
            )
        )

    def disconnect_account(
        self, workspace_id: str, connection_id: str, member_id: str, *, now: datetime
    ) -> ThreadsAccount:
        account = self.accounts.require_owned(workspace_id, connection_id, member_id)
        updated = self.accounts.put(
            account.model_copy(update={"status": ThreadsAccountStatus.REVOKED, "updated_at": now})
        )
        self.tokens.delete(account.token_ref)
        return updated

    def _consume(self, state_id: str, *, now: datetime) -> tuple[str, str, tuple[str, ...]]:
        with sqlite3.connect(self.database_path) as database:
            _ = database.execute("BEGIN IMMEDIATE")
            row = _STATE_ROW.validate_python(
                database.execute(
                    """SELECT workspace_id,member_id,redirect_uri,requested_scopes,
                    expires_at,consumed FROM threads_oauth_states WHERE state_id=?""",
                    (state_id,),
                ).fetchone()
            )
            if row is None:
                raise ThreadsOAuthError("threads_oauth_state_not_found")
            workspace_id, member_id, redirect_uri, scopes, expires_at, consumed = row
            if consumed != 0:
                raise ThreadsOAuthError("threads_oauth_state_consumed")
            if redirect_uri != self.redirect_uri:
                raise ThreadsOAuthError("threads_oauth_redirect_changed")
            if datetime.fromisoformat(expires_at) <= now:
                raise ThreadsOAuthError("threads_oauth_state_expired")
            changed = database.execute(
                """UPDATE threads_oauth_states SET consumed=1
                WHERE state_id=? AND consumed=0""",
                (state_id,),
            ).rowcount
            if changed != 1:
                raise ThreadsOAuthError("threads_oauth_state_consumed")
        return workspace_id, member_id, tuple(scopes.split(","))


__all__ = ["ThreadsConnectLink", "ThreadsOAuthError", "ThreadsOAuthService"]
