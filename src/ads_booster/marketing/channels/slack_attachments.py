"""Scoped Slack attachment capabilities and credential-owning byte fetches."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPConnection, HTTPSConnection
from typing import Annotated, Final, Never, final, override
from urllib.parse import urlsplit

from pydantic import Field

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.models import ContractModel
from ads_booster.knowledge.source_contracts import AttachmentCapability
from ads_booster.knowledge.source_fetch import (
    FetchedSource,
    SourceFetchRequest,
    sanitize_persisted_url,
)
from ads_booster.transport.json_types import JsonObject

_MAX_ATTACHMENTS = 8


class SlackAttachment(ContractModel):
    file_id: Annotated[str, Field(pattern=r"^F[A-Z0-9]{1,80}$")]
    name: Annotated[str, Field(max_length=240)] = ""
    media_type: Annotated[str, Field(max_length=120)] = ""


def attachment_references(event: JsonObject) -> tuple[SlackAttachment, ...]:
    values = event.get("files", [])
    if not isinstance(values, list) or len(values) > _MAX_ATTACHMENTS:
        raise ValueError("slack_attachments_invalid")
    result: list[SlackAttachment] = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("slack_attachment_invalid")
        result.append(
            SlackAttachment.model_validate(
                {
                    "file_id": value.get("id"),
                    "name": value.get("name", ""),
                    "media_type": value.get("mimetype", ""),
                }
            )
        )
    if len({item.file_id for item in result}) != len(result):
        raise ValueError("slack_attachment_duplicate")
    return tuple(result)


_DEFAULT_MAX_BYTES: Final = 52_428_800
_HTTP_OK: Final = 200
_HTTP_REDIRECT_START: Final = 300
_HTTP_REDIRECT_END: Final = 400


@dataclass(slots=True)
class SlackAttachmentFetchError(RuntimeError):
    """A bounded attachment fetch failed without exposing its credential or URL."""

    code: str
    retryable: bool = False

    @override
    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class SlackAttachmentContext:
    workspace_id: str
    message_id: str
    files: tuple[JsonObject, ...]


@dataclass(frozen=True, slots=True)
class FetchedSlackAttachment:
    capability_ref: str
    mime_type: str
    body: bytes


@dataclass(frozen=True, slots=True)
class SlackAttachmentFetcher:
    bot_token: str
    allowed_hosts: frozenset[str] = frozenset({"files.slack.com"})
    allowed_schemes: frozenset[str] = frozenset({"https"})
    max_bytes: int = _DEFAULT_MAX_BYTES
    timeout_seconds: float = 10.0

    def fetch(self, attachment: AttachmentCapability) -> FetchedSlackAttachment:
        if attachment.source_url is None or attachment.capability_ref is None:
            raise SlackAttachmentFetchError("slack_attachment_capability_incomplete")
        parsed = urlsplit(attachment.source_url)
        if (
            parsed.scheme not in self.allowed_schemes
            or parsed.hostname not in self.allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise SlackAttachmentFetchError("slack_attachment_origin_rejected")
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        try:
            connection_type = HTTPSConnection if parsed.scheme == "https" else HTTPConnection
            with closing(
                connection_type(parsed.hostname, port=parsed.port, timeout=self.timeout_seconds)
            ) as connection:
                connection.request(
                    "GET", target, headers={"Authorization": f"Bearer {self.bot_token}"}
                )
                response = connection.getresponse()
                if _HTTP_REDIRECT_START <= response.status < _HTTP_REDIRECT_END:
                    _fail("slack_attachment_redirect_rejected")
                if response.status != _HTTP_OK:
                    _fail("slack_attachment_fetch_failed")
                declared = response.getheader("Content-Length")
                if declared is not None and int(declared) > self.max_bytes:
                    _fail("slack_attachment_too_large")
                response_type = response.getheader("Content-Type") or attachment.mime_type
                mime_type = response_type.partition(";")[0].strip().lower()
                body = response.read(self.max_bytes + 1)
        except SlackAttachmentFetchError:
            raise
        except (OSError, TimeoutError, ValueError) as error:
            raise SlackAttachmentFetchError(
                "slack_attachment_fetch_failed", retryable=True
            ) from error
        if len(body) > self.max_bytes:
            raise SlackAttachmentFetchError("slack_attachment_too_large")
        return FetchedSlackAttachment(
            capability_ref=attachment.capability_ref,
            mime_type=mime_type,
            body=body,
        )


@final
class SlackScopedSourceFetcher:
    def __init__(self, attachment_fetcher: SlackAttachmentFetcher) -> None:
        self._attachment_fetcher = attachment_fetcher

    def fetch(self, request: SourceFetchRequest) -> FetchedSource:
        fetched = self._attachment_fetcher.fetch(
            AttachmentCapability(
                ordinal=0,
                logical_source_ref="slack-fetch-source",
                logical_revision_ref="slack-fetch-revision",
                mime_type="application/octet-stream",
                capability_ref="slack-fetch-capability",
                source_url=request.url,
            )
        )
        sanitized_url = sanitize_persisted_url(request.url)
        return FetchedSource(
            original_url=sanitized_url,
            final_url=sanitized_url,
            status_code=200,
            body=fetched.body,
            mime_type=fetched.mime_type,
            etag=None,
            last_modified=None,
            fetched_at=datetime.now(UTC),
            not_modified=False,
        )


def attachment_capabilities(context: SlackAttachmentContext) -> tuple[AttachmentCapability, ...]:
    capabilities: list[AttachmentCapability] = []
    for ordinal, item in enumerate(context.files):
        file_id = item.get("id")
        private_url = item.get("url_private_download") or item.get("url_private")
        mime_type = item.get("mimetype", "application/octet-stream")
        if not isinstance(file_id, str) or not isinstance(private_url, str):
            continue
        if not isinstance(mime_type, str):
            mime_type = "application/octet-stream"
        source_key = contract_sha256(
            {
                "workspace_id": context.workspace_id,
                "message_id": context.message_id,
                "file_id": file_id,
            }
        )
        capabilities.append(
            AttachmentCapability(
                ordinal=ordinal,
                logical_source_ref="slack-source-" + source_key[:40],
                logical_revision_ref="slack-revision-" + source_key[40:],
                mime_type=mime_type,
                capability_ref=file_id,
                source_url=private_url,
            )
        )
    return tuple(capabilities)


def _fail(code: str) -> Never:
    raise SlackAttachmentFetchError(code, retryable=code == "slack_attachment_fetch_failed")


__all__ = [
    "FetchedSlackAttachment",
    "SlackAttachmentContext",
    "SlackAttachmentFetchError",
    "SlackAttachmentFetcher",
    "SlackScopedSourceFetcher",
    "attachment_capabilities",
]
