from __future__ import annotations

import json
from hashlib import sha256
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject


def build_codex_appium_process_arguments(
    request_sha256: str,
    export_nonce: str,
    device_udid: str,
) -> tuple[str, ...]:
    return (
        "-traceMarketingAutomation",
        "-traceMarketingExportWallpaper",
        "-traceMarketingRequestDigest",
        request_sha256,
        "-traceMarketingExportNonce",
        export_nonce,
        "-traceMarketingDeviceUDID",
        device_udid,
    )


def canonical_json_digest(payload: JsonObject) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(serialized.encode("utf-8")).hexdigest()
