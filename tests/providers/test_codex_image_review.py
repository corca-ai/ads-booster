from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from PIL import Image
from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.tools.image_review import review_images
from ads_booster.providers.codex_cli import CodexCli, CodexCliError, read_review_images
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonValue

_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def response(*, source_index: int = 0) -> JsonObject:
    return {
        "schema_version": "trace.image-visual-assessment.v1",
        "summary": "Synthetic fixture: reserve more space above the calendar.",
        "findings": [
            {
                "source_index": source_index,
                "area": "whitespace",
                "severity": "warning",
                "location": "top third",
                "observation": "Limited text space",
                "recommendation": "Extend the top while preserving the character",
            }
        ],
        "uncertainties": ["Actual device readability requires human review"],
        "human_questions": ["Does this mood match the campaign?"],
    }


def png(path: Path, *, size: tuple[int, int] = (64, 96)) -> Path:
    with Image.new("RGB", size, "white") as image:
        image.save(path, format="PNG")
    return path


def test_official_image_flags_no_tools_and_byte_bound_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = png(tmp_path / "authorized-source.png")
    source_bytes = source.read_bytes()
    calls: list[list[str]] = []
    prompts: list[str] = []
    schemas: list[JsonObject] = []

    def run(command: list[str], **kwargs: JsonValue) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        prompts.append(str(kwargs["input"]))
        workspace = Path(command[command.index("--cd") + 1])
        attached = [Path(command[i + 1]) for i, value in enumerate(command) if value == "--image"]
        assert attached == [workspace / "source-0.png"]
        assert attached[0].read_bytes() == source_bytes
        assert attached[0].stat().st_mode & 0o777 == 0o600
        assert workspace.stat().st_mode & 0o777 == 0o700
        receipt = workspace / "codex-marketing-image-review-invocation.json"
        assert receipt.stat().st_mode & 0o777 == 0o600
        schemas.append(
            _JSON.validate_json(Path(command[command.index("--output-schema") + 1]).read_text())
        )
        output = Path(command[command.index("--output-last-message") + 1])
        _ = output.write_text(json.dumps(response()))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)
    result = review_images(
        CodexCli(tmp_path / "codex", model="fixture-model"),
        images=(source,),
        request="캐릭터를 유지하고 여백을 평가해줘",
        workspace_root=tmp_path / "reviews",
    )
    command = calls[0]
    assert len(calls) == 1
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--model") + 1] == "fixture-model"
    assert {"--ephemeral", "--ignore-user-config", "--ignore-rules"} <= set(command)
    disabled = {command[i + 1] for i, value in enumerate(command) if value == "--disable"}
    assert {
        "shell_tool",
        "unified_exec",
        "image_generation",
        "apps",
        "hooks",
        "multi_agent",
    } <= disabled
    assert "--search" not in command
    assert not list((tmp_path / "reviews").iterdir())
    assert source.read_bytes() == source_bytes
    assert result["human_review"] == {"status": "required", "final_approval": False}
    assert result["product_support_verified"] is False
    receipt = result["receipt"]
    assert isinstance(receipt, dict)
    assert receipt["source_sha256s"] == [hashlib.sha256(source_bytes).hexdigest()]
    assert receipt["prompt_sha256"] == hashlib.sha256(prompts[0].encode()).hexdigest()
    assert receipt["schema_sha256"] == contract_sha256(schemas[0])
    assert receipt["assessment_sha256"] == contract_sha256(response())


@pytest.mark.parametrize(
    "kind", ["empty", "many", "bytes", "pixels", "format", "corrupt", "symlink"]
)
def test_invalid_images_never_start_codex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        pytest.fail("Invalid image must not dispatch a provider process")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", forbidden)
    image = tmp_path / "source.png"
    if kind == "bytes":
        with image.open("wb") as stream:
            _ = stream.truncate(10 * 1024 * 1024 + 1)
    elif kind == "pixels":
        _ = png(image, size=(5001, 4000))
    elif kind == "format":
        with Image.new("RGB", (2, 2)) as content:
            content.save(image, format="GIF")
    elif kind == "corrupt":
        _ = image.write_bytes(b"not a PNG")
    elif kind == "symlink":
        image.symlink_to(png(tmp_path / "actual.png"))
    else:
        _ = png(image)
    images = () if kind == "empty" else (image,) * (5 if kind == "many" else 1)
    with pytest.raises(CodexCliError, match="codex_image_review"):
        _ = CodexCli(tmp_path / "codex").run_marketing_image_review_job(
            "Review",
            {"type": "object"},
            images=images,
            workspace=tmp_path,
            timeout_seconds=10,
        )


def test_jpeg_decodes_and_outside_workspace_is_rejected(tmp_path: Path) -> None:
    image = tmp_path / "source.jpg"
    with Image.new("RGB", (32, 48)) as content:
        content.save(image, format="JPEG")
    decoded = read_review_images((image,))
    assert decoded[0].format == "JPEG"
    assert (decoded[0].width, decoded[0].height) == (32, 48)
    workspace = tmp_path / "work"
    workspace.mkdir()
    with pytest.raises(CodexCliError, match="workspace_or_receipt_invalid"):
        _ = CodexCli(tmp_path / "codex").run_marketing_image_review_job(
            "Review",
            {"type": "object"},
            images=(image,),
            workspace=workspace,
            timeout_seconds=10,
        )


def test_model_findings_cannot_reference_an_unattached_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(command: list[str], **_kwargs: JsonValue) -> subprocess.CompletedProcess[str]:
        output = Path(command[command.index("--output-last-message") + 1])
        _ = output.write_text(json.dumps(response(source_index=3)))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("ads_booster.providers.codex_cli.subprocess.run", run)
    with pytest.raises(ValueError, match="image_review_source_index_invalid"):
        _ = review_images(
            CodexCli(tmp_path / "codex", model="fixture-model"),
            images=(png(tmp_path / "source.png"),),
            request="Review the calendar",
            workspace_root=tmp_path / "reviews",
        )
