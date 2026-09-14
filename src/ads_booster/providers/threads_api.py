from __future__ import annotations

import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, override

import httpx2
from pydantic import BaseModel, TypeAdapter, ValidationError

from ..contracts.threads import ThreadsMetric, ThreadsPost
from .threads_api_contracts import (
    ApiErrorResponse,
    ContainerStatusResponse,
    DebugTokenResponse,
    IdResponse,
    InsightsResponse,
    PostsResponse,
    ThreadsApiPost,
    ThreadsUserResponse,
    TokenResponse,
)

_API_ROOT: Final = "https://graph.threads.net"
_API_VERSION: Final = "v1.0"
_LIMITS: Final = httpx2.Limits(
    max_connections=50,
    max_keepalive_connections=20,
    keepalive_expiry=60.0,
)
_TIMEOUT: Final = httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)
_SOCKET_OPTIONS: Final = [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]


@dataclass(frozen=True, slots=True)
class ThreadsApiError(Exception):
    status: int | None
    code: int | None
    message: str
    retry_after_seconds: int | None = None
    uncertain_effect: bool = False

    @override
    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ThreadsToken:
    access_token: str
    provider_account_id: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ThreadsPage:
    posts: tuple[ThreadsPost, ...]
    after: str | None


@dataclass(slots=True)
class ThreadsApiClient:
    app_id: str
    app_secret: str
    client: httpx2.Client
    sleeper: Callable[[float], None] = time.sleep

    @classmethod
    def create(cls, *, app_id: str, app_secret: str) -> ThreadsApiClient:
        transport = httpx2.HTTPTransport(
            http2=True,
            retries=0,
            limits=_LIMITS,
            socket_options=_SOCKET_OPTIONS,
        )
        client = httpx2.Client(
            base_url=_API_ROOT,
            transport=transport,
            timeout=_TIMEOUT,
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "trace-marketing"},
        )
        return cls(app_id=app_id, app_secret=app_secret, client=client)

    def close(self) -> None:
        self.client.close()

    def exchange_code(self, *, code: str, redirect_uri: str, now: datetime) -> ThreadsToken:
        payload = self._request_model(
            "POST",
            "/oauth/access_token",
            TokenResponse,
            params={
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        provider_id = str(payload.user_id) if payload.user_id is not None else "me"
        short_expiry = payload.expires_in or 3_600
        return ThreadsToken(payload.access_token, provider_id, now + timedelta(seconds=short_expiry))

    def exchange_long_lived(self, token: ThreadsToken, *, now: datetime) -> ThreadsToken:
        payload = self._request_model(
            "GET",
            "/access_token",
            TokenResponse,
            token=token.access_token,
            params={
                "grant_type": "th_exchange_token",
                "client_secret": self.app_secret,
            },
        )
        expiry = now + timedelta(seconds=payload.expires_in or 5_184_000)
        return ThreadsToken(payload.access_token, token.provider_account_id, expiry)

    def refresh(self, token: ThreadsToken, *, now: datetime) -> ThreadsToken:
        payload = self._request_model(
            "GET",
            "/refresh_access_token",
            TokenResponse,
            token=token.access_token,
            params={"grant_type": "th_refresh_token"},
        )
        expiry = now + timedelta(seconds=payload.expires_in or 5_184_000)
        return ThreadsToken(payload.access_token, token.provider_account_id, expiry)

    def user(self, token: str) -> ThreadsUserResponse:
        return self._request_model(
            "GET",
            f"/{_API_VERSION}/me",
            ThreadsUserResponse,
            token=token,
            params={"fields": "id,username"},
        )

    def granted_scopes(self, token: str) -> tuple[str, ...]:
        payload = self._request_model(
            "GET",
            "/debug_token",
            DebugTokenResponse,
            params={
                "input_token": token,
                "access_token": f"{self.app_id}|{self.app_secret}",
            },
        )
        if not payload.data.is_valid:
            raise ThreadsApiError(401, None, "threads_access_token_invalid")
        return payload.data.scope_names()

    def post(self, token: str, post_id: str, *, account_id: str) -> ThreadsPost:
        payload = self._request_model(
            "GET",
            f"/{_API_VERSION}/{post_id}",
            ThreadsApiPost,
            token=token,
            params={"fields": _post_fields()},
        )
        return _post(payload, account_id=account_id)

    def posts(
        self,
        token: str,
        *,
        account_id: str,
        limit: int = 25,
        after: str | None = None,
    ) -> ThreadsPage:
        params = {"fields": _post_fields(), "limit": str(limit)}
        if after is not None:
            params["after"] = after
        payload = self._request_model(
            "GET", f"/{_API_VERSION}/me/threads", PostsResponse, token=token, params=params
        )
        cursor = None if payload.paging is None else payload.paging.cursors.after
        return ThreadsPage(
            tuple(_post(item, account_id=account_id) for item in payload.data), cursor
        )

    def search(
        self,
        token: str,
        *,
        query: str,
        search_type: Literal["TOP", "RECENT"] = "RECENT",
        limit: int = 25,
        after: str | None = None,
        account_id: str,
    ) -> ThreadsPage:
        params = {
            "q": query,
            "search_type": search_type,
            "limit": str(limit),
            "fields": _post_fields(),
        }
        if after is not None:
            params["after"] = after
        payload = self._request_model(
            "GET", f"/{_API_VERSION}/keyword_search", PostsResponse, token=token, params=params
        )
        cursor = None if payload.paging is None else payload.paging.cursors.after
        return ThreadsPage(
            tuple(_post(item, account_id=account_id) for item in payload.data), cursor
        )

    def conversation(
        self,
        token: str,
        *,
        post_id: str,
        account_id: str,
        limit: int = 25,
        after: str | None = None,
    ) -> ThreadsPage:
        params = {"fields": _post_fields(), "limit": str(limit)}
        if after is not None:
            params["after"] = after
        payload = self._request_model(
            "GET",
            f"/{_API_VERSION}/{post_id}/conversation",
            PostsResponse,
            token=token,
            params=params,
        )
        cursor = None if payload.paging is None else payload.paging.cursors.after
        return ThreadsPage(
            tuple(_post(item, account_id=account_id) for item in payload.data), cursor
        )

    def replies(
        self,
        token: str,
        *,
        post_id: str,
        account_id: str,
        limit: int = 25,
        after: str | None = None,
    ) -> ThreadsPage:
        params = {"fields": _post_fields(), "limit": str(limit)}
        if after is not None:
            params["after"] = after
        payload = self._request_model(
            "GET",
            f"/{_API_VERSION}/{post_id}/replies",
            PostsResponse,
            token=token,
            params=params,
        )
        cursor = None if payload.paging is None else payload.paging.cursors.after
        return ThreadsPage(
            tuple(_post(item, account_id=account_id) for item in payload.data), cursor
        )

    def insights(
        self, token: str, *, subject_id: str, metrics: tuple[str, ...]
    ) -> tuple[ThreadsMetric, ...]:
        payload = self._request_model(
            "GET",
            f"/{_API_VERSION}/{subject_id}/insights",
            InsightsResponse,
            token=token,
            params={"metric": ",".join(metrics)},
        )
        return tuple(
            ThreadsMetric(
                name=item.name,
                period=item.period,
                value=item.scalar(),
                available=item.scalar() is not None,
            )
            for item in payload.data
        )

    def account_insights(
        self, token: str, *, metrics: tuple[str, ...]
    ) -> tuple[ThreadsMetric, ...]:
        payload = self._request_model(
            "GET",
            f"/{_API_VERSION}/me/threads_insights",
            InsightsResponse,
            token=token,
            params={"metric": ",".join(metrics)},
        )
        return tuple(
            ThreadsMetric(
                name=item.name,
                period=item.period,
                value=item.scalar(),
                available=item.scalar() is not None,
            )
            for item in payload.data
        )

    def create_image_container(
        self, token: str, *, image_url: str, alt_text: str
    ) -> str:
        payload = self._request_model(
            "POST",
            f"/{_API_VERSION}/me/threads",
            IdResponse,
            token=token,
            params={
                "media_type": "IMAGE",
                "image_url": image_url,
                "is_carousel_item": "true",
                "alt_text": alt_text,
            },
        )
        return payload.id

    def create_carousel(
        self, token: str, *, children: tuple[str, ...], text: str, reply_to_id: str | None = None
    ) -> str:
        params = {"media_type": "CAROUSEL", "children": ",".join(children), "text": text}
        if reply_to_id is not None:
            params["reply_to_id"] = reply_to_id
        payload = self._request_model(
            "POST", f"/{_API_VERSION}/me/threads", IdResponse, token=token, params=params
        )
        return payload.id

    def create_text(self, token: str, *, text: str, reply_to_id: str | None = None) -> str:
        params = {"media_type": "TEXT", "text": text, "auto_publish_text": "false"}
        if reply_to_id is not None:
            params["reply_to_id"] = reply_to_id
        payload = self._request_model(
            "POST", f"/{_API_VERSION}/me/threads", IdResponse, token=token, params=params
        )
        return payload.id

    def publish(self, token: str, *, creation_id: str) -> str:
        payload = self._request_model(
            "POST",
            f"/{_API_VERSION}/me/threads_publish",
            IdResponse,
            token=token,
            params={"creation_id": creation_id},
        )
        return payload.id

    def wait_until_ready(
        self, token: str, *, creation_id: str, attempts: int = 10
    ) -> None:
        if attempts < 1 or attempts > 30:
            raise ValueError("threads_container_attempts_invalid")
        for attempt in range(attempts):
            payload = self._request_model(
                "GET",
                f"/{_API_VERSION}/{creation_id}",
                ContainerStatusResponse,
                token=token,
                params={"fields": "id,status,error_message"},
            )
            match payload.status.upper():
                case "FINISHED":
                    return
                case "ERROR" | "EXPIRED":
                    raise ThreadsApiError(
                        422,
                        None,
                        payload.error_message or "threads_container_processing_failed",
                    )
                case _ if attempt + 1 < attempts:
                    self.sleeper(2.0)
                case _:
                    raise ThreadsApiError(408, None, "threads_container_processing_timeout")

    def _request_model[ModelT: BaseModel](
        self,
        method: Literal["GET", "POST"],
        path: str,
        model: type[ModelT],
        *,
        token: str | None = None,
        params: dict[str, str],
    ) -> ModelT:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        response = None
        for attempt in range(2):
            try:
                response = self.client.request(
                    method,
                    path,
                    params=params if method == "GET" else None,
                    data=params if method == "POST" else None,
                    headers=headers,
                )
            except httpx2.TimeoutException as error:
                if method == "GET" and attempt == 0:
                    continue
                raise ThreadsApiError(
                    None, None, "threads_api_timeout", uncertain_effect=method == "POST"
                ) from error
            except httpx2.NetworkError as error:
                if method == "GET" and attempt == 0:
                    continue
                raise ThreadsApiError(
                    None,
                    None,
                    "threads_api_network_error",
                    uncertain_effect=method == "POST",
                ) from error
            if method == "GET" and attempt == 0 and response.status_code in {502, 503, 504}:
                continue
            break
        if response is None:
            raise ThreadsApiError(None, None, "threads_api_response_missing")
        if response.status_code >= 400:
            error_body = None
            try:
                error_body = ApiErrorResponse.model_validate_json(response.content)
            except ValidationError:
                pass
            retry_after = response.headers.get("retry-after")
            raise ThreadsApiError(
                response.status_code,
                None if error_body is None else error_body.error.code,
                "threads_api_request_failed"
                if error_body is None
                else error_body.error.message[:500],
                int(retry_after) if retry_after is not None and retry_after.isdigit() else None,
                method == "POST" and response.status_code >= 500,
            )
        try:
            return TypeAdapter(model).validate_json(response.content)
        except ValidationError as error:
            raise ThreadsApiError(
                response.status_code,
                None,
                "threads_api_response_invalid",
                uncertain_effect=method == "POST",
            ) from error


def _post_fields() -> str:
    return (
        "id,username,text,permalink,media_type,timestamp,has_replies,"
        "root_post,replied_to"
    )


def _post(payload: ThreadsApiPost, *, account_id: str) -> ThreadsPost:
    timestamp = datetime.fromisoformat(payload.timestamp.replace("Z", "+00:00")).astimezone(UTC)
    return ThreadsPost(
        post_id=payload.id,
        account_id=account_id,
        username=payload.username,
        text=payload.text,
        permalink=payload.permalink,
        media_type=payload.media_type,
        timestamp=timestamp,
        root_post_id=None if payload.root_post is None else payload.root_post.id,
        replied_to_id=None if payload.replied_to is None else payload.replied_to.id,
        has_replies=payload.has_replies,
    )


__all__ = ["ThreadsApiClient", "ThreadsApiError", "ThreadsPage", "ThreadsToken"]
