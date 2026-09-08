"""Signed-file scope, bounded downloads and actual image-review helper with fake inference."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from PIL import Image
from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256
from ads_booster.marketing.agent_service.slack_image_review import (
    SlackImageReviewTool,
    bind_files,
    slack_image_review_descriptor,
)
from ads_booster.providers.codex_cli import CodexCli
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from urllib.request import Request

NOW = datetime(2026, 9, 7, tzinfo=UTC)
_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_OBJECTS = TypeAdapter(list[JsonObject])
_STR = TypeAdapter(str)


@dataclass
class Response:
    data: bytes
    url: str
    closed: bool = False

    def read(self, size: int = -1) -> bytes:
        return self.data[:size] if size >= 0 else self.data

    def geturl(self) -> str:
        return self.url

    def close(self) -> None:
        self.closed = True


@dataclass
class HTTP:
    image: bytes
    download_url: str = "https://files.slack.com/files-pri/T00-F01/source.png"
    response_url: str = ""
    file_id: str = "F01"
    team: str = "T00"
    calls: list[str] = field(default_factory=list)
    responses: list[Response] = field(default_factory=list)

    def __call__(self, request: Request, *, timeout: float) -> Response:
        assert timeout == 15
        assert request.get_header("Authorization") == "Bearer synthetic-test-token"
        self.calls.append(request.full_url)
        if request.full_url.startswith("https://slack.com/api/files.info?"):
            data = json.dumps(
                {
                    "ok": True,
                    "file": {
                        "id": self.file_id,
                        "team_id": self.team,
                        "url_private_download": self.download_url,
                    },
                }
            ).encode()
            result = Response(data, request.full_url)
        else:
            result = Response(self.image, self.response_url or request.full_url)
        self.responses.append(result)
        return result


def png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 128), "white").save(output, format="PNG")
    return output.getvalue()


def invocation(
    *, run_id: str = "run-1", file_ids: tuple[str, ...] = ("F01",), tenant_id: str | None = "team"
) -> ToolInvocation:
    payload: JsonObject = {
        "file_ids": list(file_ids),
        "request": "달력은 유지하고 위 여백을 검토해줘",
    }
    return ToolInvocation(
        tenant_id=tenant_id,
        schema_version="trace.tool-invocation.v1",
        invocation_id="invocation-1",
        run_id=run_id,
        step_id="step-1",
        intent_sha256="a" * 64,
        capability_snapshot_sha256="b" * 64,
        descriptor_sha256="c" * 64,
        idempotency_key="stable-key",
        input=payload,
        input_sha256=contract_sha256(payload),
    )


def tool(tmp_path: Path, http: HTTP, *, tenant: str = "team") -> SlackImageReviewTool:
    return SlackImageReviewTool(
        tmp_path / "state.sqlite",
        tmp_path / "artifacts",
        tenant,
        "synthetic-test-token",
        CodexCli(Path("/synthetic/codex"), model="synthetic"),
        expected_team_id="T00",
        opener=http,
    )


def test_all_files_authorized_before_network_and_other_scopes_denied(tmp_path: Path) -> None:
    http = HTTP(png())
    instance = tool(tmp_path, http)
    bind_files(instance.database_path, "team", "run-1", "C00", ("F01",))
    descriptor = slack_image_review_descriptor(now=NOW, ready=True)
    for task in [invocation(run_id="other"), invocation(file_ids=("F01", "F02"))]:
        with pytest.raises(ValueError, match="not_bound"):
            _ = instance.execute(task, descriptor)
    with pytest.raises(ValueError, match="not_bound"):
        _ = tool(tmp_path, http, tenant="other").execute(invocation(tenant_id="other"), descriptor)
    assert http.calls == []

    assert not instance.artifact_root.exists()
    with pytest.raises(ValueError, match="binding_conflict"):
        bind_files(instance.database_path, "team", "run-1", "C02", ("F01",))


@pytest.mark.parametrize("tenant_id", ["other", None])
def test_foreign_or_unscoped_invocation_cannot_use_bound_run_files(
    tmp_path: Path, tenant_id: str | None
) -> None:
    http = HTTP(png())
    instance = tool(tmp_path, http)
    bind_files(instance.database_path, "team", "run-1", "C1", ("F01",))
    with pytest.raises(ValueError, match="slack_image_invocation_tenant_mismatch"):
        _ = instance.execute(
            invocation(tenant_id=tenant_id), slack_image_review_descriptor(now=NOW, ready=True)
        )
    assert http.calls == []


@pytest.mark.parametrize(
    "url",
    [
        "http://files.slack.com/a",
        "https://evil.example/a",
        "https://files.slack.com.evil.example/a",
        "https://user@files.slack.com/a",
        "https://files.slack.com:444/a",
    ],
)
def test_untrusted_download_targets_never_receive_token(tmp_path: Path, url: str) -> None:
    http = HTTP(png(), download_url=url)
    instance = tool(tmp_path, http)
    bind_files(instance.database_path, "team", "run-1", "C00", ("F01",))
    with pytest.raises(ValueError, match="slack_image_review_failed"):
        _ = instance.execute(invocation(), slack_image_review_descriptor(now=NOW, ready=True))
    assert len(http.calls) == 1
    assert all(response.closed for response in http.responses)


@pytest.mark.parametrize(
    "failure", ["redirect", "bad_bytes", "oversized", "wrong_file", "wrong_team"]
)
def test_download_failure_is_sanitized_and_never_retried(tmp_path: Path, failure: str) -> None:
    http = HTTP(png())
    if failure == "redirect":
        http.response_url = "https://evil.example/redirected"
    elif failure == "bad_bytes":
        http.image = b"not an image"
    elif failure == "oversized":
        http.image = b"x" * (10 * 1024 * 1024 + 1)
    elif failure == "wrong_file":
        http.file_id = "F02"
    else:
        http.team = "TOTHER"
    instance = tool(tmp_path, http)
    bind_files(instance.database_path, "team", "run-1", "C00", ("F01",))
    descriptor = slack_image_review_descriptor(now=NOW, ready=True)
    with pytest.raises(ValueError, match="slack_image_review_failed") as error:
        _ = instance.execute(invocation(), descriptor)
    assert "synthetic-test-token" not in str(error.value)
    calls = len(http.calls)
    with pytest.raises(ValueError, match="outcome_unresolved"):
        _ = tool(tmp_path, http).execute(invocation(), descriptor)
    assert len(http.calls) == calls
    assert all(response.closed for response in http.responses)


def test_real_png_passes_actual_review_helper_with_hash_lineage_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Path, ...]] = []

    def review(  # noqa: PLR0913 - actual Codex image review method signature.
        self: CodexCli,
        prompt: str,
        schema: JsonObject,
        *,
        images: tuple[Path, ...],
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        _ = self, schema, workspace, timeout_seconds
        assert "달력은 유지" in prompt
        with Image.open(images[0]) as image:
            assert image.size == (64, 128)
        seen.append(images)
        return {
            "schema_version": "trace.image-visual-assessment.v1",
            "summary": "여백 확인",
            "findings": [],
            "uncertainties": ["실제 폰 검수 필요"],
            "human_questions": [],
        }

    monkeypatch.setattr(CodexCli, "run_marketing_image_review_job", review)
    http = HTTP(png())
    instance = tool(tmp_path, http)
    bind_files(instance.database_path, "team", "run-1", "C00", ("F01",))
    descriptor = slack_image_review_descriptor(now=NOW, ready=True)
    result = instance.execute(invocation(), descriptor)
    source = _OBJECTS.validate_python(result.output["sources"])[0]
    saved = instance.artifact_root / _STR.validate_python(source["artifact_relative_path"])
    assert saved.read_bytes() == http.image
    assert saved.stat().st_mode & 0o777 == 0o600
    reviewed = _OBJECT.validate_python(result.output["review"])
    assert reviewed["product_support_verified"] is False
    quantitative = _OBJECTS.validate_python(reviewed["quantitative_checks"])[0]
    assert quantitative["decode_verified"] is True
    assert quantitative["sha256"] == source["sha256"]
    assert instance.execute(invocation(), descriptor) == result
    assert len(seen) == 1
    assert len(http.calls) == 2
    assert "synthetic-test-token" not in repr(instance)
    assert "files.slack.com" not in json.dumps(result.output)


def test_eight_signed_refs_bind_but_review_batch_is_limited_to_four(tmp_path: Path) -> None:
    instance = tool(tmp_path, HTTP(png()))
    file_ids = tuple(f"F{index}" for index in range(8))
    bind_files(instance.database_path, "team", "run-1", "C1", file_ids)
    with pytest.raises(ValueError, match="at most 4"):
        _ = instance.execute(
            invocation(file_ids=file_ids), slack_image_review_descriptor(now=NOW, ready=True)
        )


def test_image_above_pixel_limit_is_rejected_before_inference(tmp_path: Path) -> None:
    output = io.BytesIO()
    Image.new("RGB", (4096, 5000), "white").save(output, format="PNG")
    http = HTTP(output.getvalue())
    instance = tool(tmp_path, http)
    bind_files(instance.database_path, "team", "run-1", "C1", ("F01",))
    with pytest.raises(ValueError, match="slack_image_review_failed"):
        _ = instance.execute(invocation(), slack_image_review_descriptor(now=NOW, ready=True))
    assert len(http.calls) == 2


def test_inference_response_loss_is_not_replayed_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fail(self: CodexCli, prompt: str, schema: JsonObject, **kwargs: object) -> JsonObject:
        _ = self, prompt, schema, kwargs
        calls.append("inference")
        message = "synthetic_provider_internal_error"
        raise RuntimeError(message)

    monkeypatch.setattr(CodexCli, "run_marketing_image_review_job", fail)
    http = HTTP(png())
    instance = tool(tmp_path, http)
    bind_files(instance.database_path, "team", "run-1", "C1", ("F01",))
    descriptor = slack_image_review_descriptor(now=NOW, ready=True)
    with pytest.raises(ValueError, match="slack_image_review_failed"):
        _ = instance.execute(invocation(), descriptor)
    with pytest.raises(ValueError, match="outcome_unresolved"):
        _ = tool(tmp_path, http).execute(invocation(), descriptor)
    assert calls == ["inference"]
    assert len(http.calls) == 2
