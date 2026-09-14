"""Deliver verified image drafts only to their authorized Slack conversation."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from pydantic import TypeAdapter

from ads_booster.channels.slack_image_results import IMAGE_CAPABILITIES, SlackImage, image_result
from ads_booster.contracts.agent_run import AgentRecordKind, contract_sha256
from ads_booster.tools.github_issues import NoRedirect
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ads_booster.channels.slack_conversations import Conversation
    from ads_booster.contracts.agent_run import AgentRecord
    from ads_booster.tools.github_issues import Response

_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def open_slack(request: Request, *, timeout: float) -> Response:
    return cast("Response", build_opener(NoRedirect()).open(request, timeout=timeout))


@dataclass(slots=True)
class ImageDeliveryNotice:
    text: str
    replaces_answer: bool = False


@dataclass(slots=True)
class SlackImageDelivery:
    root: Path
    database: Path
    token: str = field(repr=False)
    opener: Callable[..., Response] = open_slack

    def __post_init__(self) -> None:
        """Record upload admission before any external side effect."""
        with closing(sqlite3.connect(self.database)) as db, db:
            _ = db.execute("""CREATE TABLE IF NOT EXISTS slack_image_deliveries (
                conversation_id TEXT NOT NULL, run_id TEXT NOT NULL, digest TEXT NOT NULL,
                state TEXT NOT NULL, PRIMARY KEY(conversation_id,run_id,digest))""")

    def deliver(
        self, records: Sequence[AgentRecord], conversation: Conversation
    ) -> ImageDeliveryNotice:
        receipts = {
            contract_sha256(r.payload): r.payload
            for r in records
            if r.kind is AgentRecordKind.RECEIPT
        }
        notices: list[str] = []
        replaces_answer = False
        for record in records:
            if (
                record.kind is not AgentRecordKind.EVIDENCE
                or str(record.payload.get("capability_id")) not in IMAGE_CAPABILITIES
            ):
                continue
            output = record.payload.get("output")
            receipt = receipts.get(str(record.payload.get("receipt_sha256")))
            if (
                not isinstance(output, dict)
                or receipt is None
                or receipt.get("disposition") != "succeeded"
                or receipt.get("output_sha256") != contract_sha256(output)
            ):
                continue
            # Validate before claiming any upload, never read model-selected paths.
            try:
                images, captions = image_result(
                    self.root, self.database, record, output, conversation
                )
            except OSError, ValueError:
                notices.append("이미지 파일 검증에 실패해 첨부하지 않았습니다.")
                continue
            delivered = False
            for item in images:
                notice = self._deliver_image(conversation, record.run_id, item)
                if notice:
                    notices.append(notice)
                    delivered = True
            if delivered and captions:
                notices.append(captions)
                # Trace post's deliverable is these verified files and country captions.
                # A pre-delivery model answer cannot know the subsequent upload outcome.
                replaces_answer = True
        return ImageDeliveryNotice("\n".join(notices), replaces_answer)

    def _deliver_image(
        self,
        conversation: Conversation,
        run_id: str,
        item: SlackImage,
    ) -> str:
        key = (conversation.conversation_id, run_id, item.key)
        with closing(sqlite3.connect(self.database)) as db, db:
            claimed = db.execute(
                "INSERT OR IGNORE INTO slack_image_deliveries VALUES (?,?,?,'unknown')", key
            ).rowcount
        if not claimed:
            return ""
        try:
            self._upload(item.data, conversation, filename=item.filename, title=item.title)
            state = "delivered"
            notice = (
                f"{item.title}을 첨부했습니다. 이미지를 열어 원본 PNG를 다운로드할 수 있습니다."
            )
        except Exception:  # noqa: BLE001 - ambiguous uploads are never retried or logged with secrets.
            state = "unknown"
            notice = "\n".join(  # noqa: FLY002 - readable localized multiline notice.
                (
                    "이미지는 생성됐지만 Slack 첨부 결과를 확인하지 못했습니다.",
                    "중복 첨부를 막기 위해 자동 재업로드하지 않았습니다.",
                )
            )
        with closing(sqlite3.connect(self.database)) as db, db:
            _ = db.execute(
                "UPDATE slack_image_deliveries SET state=? "  # pyright: ignore[reportImplicitStringConcatenation]
                "WHERE conversation_id=? AND run_id=? AND digest=?",
                (state, *key),
            )
        return notice

    def _api(self, method: str, payload: JsonObject) -> JsonObject:
        if method not in {"files.getUploadURLExternal", "files.completeUploadExternal"}:
            msg = "slack_image_method_invalid"
            raise ValueError(msg)
        response = self.opener(
            Request(
                "https://slack.com/api/" + method,
                data=json.dumps(payload).encode(),
                headers={
                    "Authorization": "Bearer " + self.token,
                    "Content-Type": "application/json",
                },
                method="POST",
            ),
            timeout=30.0,
        )
        try:
            result = _JSON.validate_json(response.read())
            if response.status != HTTPStatus.OK or result.get("ok") is not True:
                msg = "slack_image_api_rejected"
                raise ValueError(msg)
            return result
        finally:
            response.close()

    def _upload(
        self,
        data: bytes,
        conversation: Conversation,
        *,
        filename: str,
        title: str,
    ) -> None:
        allocated = self._api(
            "files.getUploadURLExternal", {"filename": filename, "length": len(data)}
        )
        url, file_id = allocated.get("upload_url"), allocated.get("file_id")
        if (
            not isinstance(url, str)
            or not isinstance(file_id, str)
            or not re.fullmatch(r"F[A-Z0-9]+", file_id)
        ):
            msg = "slack_image_upload_invalid"
            raise ValueError(msg)
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.hostname != "files.slack.com"
            or parts.username
            or parts.password
            or parts.port not in {None, 443}
            or not parts.path.startswith("/upload/")
        ):
            msg = "slack_image_upload_origin_invalid"
            raise ValueError(msg)
        # The signed upload URL gets only image bytes, never the bot credential.
        response = self.opener(
            Request(  # noqa: S310 - exact HTTPS files.slack.com allowlist, no redirects.
                url, data=data, headers={"Content-Type": "application/octet-stream"}, method="POST"
            ),
            timeout=30.0,
        )
        try:
            if response.status != HTTPStatus.OK:
                msg = "slack_image_upload_failed"
                raise ValueError(msg)
        finally:
            response.close()
        payload: JsonObject = {
            "files": [{"id": file_id, "title": title}],
            "channel_id": conversation.channel_id,
        }
        if conversation.thread_ts:
            payload["thread_ts"] = conversation.thread_ts
        completed = self._api("files.completeUploadExternal", payload)
        files = completed.get("files")
        if not isinstance(files, list) or not any(
            isinstance(f, dict) and f.get("id") == file_id for f in files
        ):
            msg = "slack_image_completion_unconfirmed"
            raise ValueError(msg)
