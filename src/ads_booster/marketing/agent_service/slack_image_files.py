"""Read signed-run-bound Slack image bytes without requiring model inference."""

from __future__ import annotations

import hashlib
import io
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlencode, urlsplit
from urllib.request import Request

from PIL import Image
from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.marketing.agent_service.oauth import open_auth_request
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_PIXELS = 20_000_000
_MAX_INFO_BYTES = 512 * 1024
_MAX_URL = 4096
_FILE = re.compile(r"F[A-Z0-9]{1,79}")
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_BINDING: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)


class ReadResponse(Protocol):
    def read(self, size: int = -1) -> bytes: ...
    def geturl(self) -> str: ...
    def close(self) -> None: ...


def open_slack_image_request(request: Request, *, timeout: float) -> ReadResponse:
    return cast("ReadResponse", open_auth_request(request, timeout=timeout))


@dataclass(frozen=True, slots=True)
class SlackImageFile:
    file_id: str
    channel_id: str
    sha256: str
    path: Path
    byte_size: int


@dataclass(slots=True)
class SlackImageFiles:
    database_path: Path
    artifact_root: Path
    tenant_id: str
    token: str = field(repr=False)
    expected_team_id: str = ""
    opener: Callable[..., ReadResponse] = field(default=open_slack_image_request, repr=False)

    def fetch(self, run_id: str, file_id: str) -> SlackImageFile:
        """Resolve authenticated persisted binding before any token-bearing network call."""
        if not self.tenant_id or not self.token or not run_id or not _FILE.fullmatch(file_id):
            raise ValueError("slack_image_file_not_bound")
        try:
            with closing(
                sqlite3.connect(
                    self.database_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1
                )
            ) as db:
                binding = _BINDING.validate_python(
                    db.execute(
                        """SELECT channel FROM slack_image_bindings
                        WHERE tenant=? AND run=? AND file=?""",
                        (self.tenant_id, run_id, file_id),
                    ).fetchone()
                )
        except sqlite3.OperationalError:
            raise ValueError("slack_image_file_not_bound") from None
        if binding is None:
            raise ValueError("slack_image_file_not_bound")
        try:
            data = self._download(file_id)
            digest, suffix = _decode(data)
            root = self.artifact_root.resolve()
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory = root / contract_sha256({"tenant": self.tenant_id, "run": run_id})
            directory.mkdir(mode=0o700, exist_ok=True)
            if directory.is_symlink() or directory.resolve().parent != root:
                raise ValueError("slack_image_artifact_scope_invalid")  # noqa: TRY301 - sanitized boundary.
            path = directory / f"{digest}.{suffix}"
            _save(path, data)
        except Exception:  # noqa: BLE001 - never expose credential-bearing provider errors.
            raise ValueError("slack_image_file_fetch_failed") from None
        return SlackImageFile(file_id, binding[0], digest, path, len(data))

    def _read(self, url: str, *, max_bytes: int) -> bytes:
        response = self.opener(
            Request(url, headers={"Authorization": f"Bearer {self.token}"}),  # noqa: S310 - fixed API or validated HTTPS Slack host.
            timeout=15,
        )
        try:
            if response.geturl() != url:
                raise ValueError("slack_image_redirect_rejected")
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("slack_image_response_too_large")
            return data
        finally:
            response.close()

    def _download(self, file_id: str) -> bytes:
        info = _JSON.validate_json(
            self._read(
                "https://slack.com/api/files.info?" + urlencode({"file": file_id}),
                max_bytes=_MAX_INFO_BYTES,
            )
        )
        raw_file = info.get("file")
        if (
            info.get("ok") is not True
            or not isinstance(raw_file, dict)
            or raw_file.get("id") != file_id
        ):
            raise ValueError("slack_image_metadata_invalid")
        if self.expected_team_id and raw_file.get("team_id") not in {None, self.expected_team_id}:
            raise ValueError("slack_image_team_rejected")
        url = raw_file.get("url_private_download") or raw_file.get("url_private")
        if not isinstance(url, str) or len(url) > _MAX_URL:
            raise ValueError("slack_image_url_invalid")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "files.slack.com"
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("slack_image_url_rejected")
        return self._read(url, max_bytes=_MAX_IMAGE_BYTES)


def _decode(data: bytes) -> tuple[str, str]:
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in {"PNG", "JPEG"} or image.width * image.height > _MAX_PIXELS:
            raise ValueError("slack_image_format_or_dimensions_invalid")
        _ = image.load()
        return hashlib.sha256(data).hexdigest(), "png" if image.format == "PNG" else "jpg"


def _save(path: Path, data: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != data:
            raise ValueError("slack_image_existing_artifact_invalid") from None
    else:
        with os.fdopen(descriptor, "wb") as output:
            _ = output.write(data)
