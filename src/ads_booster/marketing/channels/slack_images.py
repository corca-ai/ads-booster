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

from ads_booster.contracts.agent_run import AgentRecordKind, contract_sha256
from ads_booster.marketing.agent_service.github_issues import NoRedirect
from ads_booster.marketing.agent_service.image_generation import CAPABILITY, read_artifact
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ads_booster.contracts.agent_run import AgentRecord
    from ads_booster.marketing.agent_service.github_issues import Response
    from ads_booster.marketing.channels.slack_conversations import Conversation

_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def open_slack(request: Request, *, timeout: float) -> Response:
    return cast("Response", build_opener(NoRedirect()).open(request, timeout=timeout))


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

    def deliver(self, records: Sequence[AgentRecord], conversation: Conversation) -> str:
        receipts = {
            contract_sha256(r.payload): r.payload
            for r in records
            if r.kind is AgentRecordKind.RECEIPT
        }
        notices: list[str] = []
        for record in records:
            if (
                record.kind is not AgentRecordKind.EVIDENCE
                or record.payload.get("capability_id") != CAPABILITY
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
            digest = str(output.get("artifact_sha256", ""))
            # Validate before claiming any upload, never read model-selected paths.
            try:
                data = read_artifact(self.root, digest)
            except OSError, ValueError:
                notices.append("이미지 파일 검증에 실패해 첨부하지 않았습니다.")
                continue
            key = (conversation.conversation_id, record.run_id, digest)
            with closing(sqlite3.connect(self.database)) as db, db:
                claimed = db.execute(
                    "INSERT OR IGNORE INTO slack_image_deliveries VALUES (?,?,?,'unknown')", key
                ).rowcount
            if not claimed:
                continue
            try:
                self._upload(data, conversation)
                state = "delivered"
                notices.append(
                    "이미지 초안을 이 대화에 첨부했습니다. 사용 전 내용을 검토해 주세요."
                )
            except Exception:  # noqa: BLE001 - ambiguous uploads are never retried or logged with secrets.
                state = "unknown"
                notices.append(
                    "\n".join(  # noqa: FLY002 - readable localized multiline notice.
                        (
                            "이미지는 생성됐지만 Slack 첨부 결과를 확인하지 못했습니다.",
                            "봇의 files:write 권한과 첨부 여부를 확인해 주세요.",
                            "자동 재업로드하지 않습니다.",
                        )
                    )
                )
            with closing(sqlite3.connect(self.database)) as db, db:
                _ = db.execute(
                    "UPDATE slack_image_deliveries SET state=? "  # pyright: ignore[reportImplicitStringConcatenation]
                    "WHERE conversation_id=? AND run_id=? AND digest=?",
                    (state, *key),
                )
        return "\n".join(notices)

    def _api(self, method: str, payload: JsonObject) -> JsonObject:
        if method not in {"files.getUploadURLExternal", "files.completeUploadExternal"}:
            raise ValueError("slack_image_method_invalid")
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
                raise ValueError("slack_image_api_rejected")
            return result
        finally:
            response.close()

    def _upload(self, data: bytes, conversation: Conversation) -> None:
        allocated = self._api(
            "files.getUploadURLExternal", {"filename": "marketing-draft.png", "length": len(data)}
        )
        url, file_id = allocated.get("upload_url"), allocated.get("file_id")
        if (
            not isinstance(url, str)
            or not isinstance(file_id, str)
            or not re.fullmatch(r"F[A-Z0-9]+", file_id)
        ):
            raise ValueError("slack_image_upload_invalid")
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.hostname != "files.slack.com"
            or parts.username
            or parts.password
            or parts.port not in {None, 443}
            or not parts.path.startswith("/upload/")
        ):
            raise ValueError("slack_image_upload_origin_invalid")
        # The signed upload URL gets only image bytes, never the bot credential.
        response = self.opener(
            Request(  # noqa: S310 - exact HTTPS files.slack.com allowlist, no redirects.
                url, data=data, headers={"Content-Type": "application/octet-stream"}, method="POST"
            ),
            timeout=30.0,
        )
        try:
            if response.status != HTTPStatus.OK:
                raise ValueError("slack_image_upload_failed")
        finally:
            response.close()
        payload: JsonObject = {
            "files": [{"id": file_id, "title": "마케팅 이미지 초안 · 검토 필요"}],
            "channel_id": conversation.channel_id,
        }
        if conversation.thread_ts:
            payload["thread_ts"] = conversation.thread_ts
        completed = self._api("files.completeUploadExternal", payload)
        files = completed.get("files")
        if not isinstance(files, list) or not any(
            isinstance(f, dict) and f.get("id") == file_id for f in files
        ):
            raise ValueError("slack_image_completion_unconfirmed")
