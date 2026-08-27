from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PIL import Image

from ads_booster.agent.session import AgentSession
from ads_booster.providers.codex import FunctionCall, ModelTurn
from ads_booster.tools.approval import DenyApproval
from ads_booster.tools.image_view import ImageViewTool
from ads_booster.tools.models import ToolContext
from ads_booster.tools.registry import default_registry

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.tools import ToolDescriptor
    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class AllowApproval:
    actions: list[tuple[str, str]] = field(default_factory=list)

    def request(self, action: str, detail: str) -> bool:
        self.actions.append((action, detail))
        return True


@dataclass(frozen=True, slots=True)
class ImageRequestModel:
    requests: list[tuple[JsonObject, ...]] = field(default_factory=list)

    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        self.requests.append(history)
        if len(self.requests) == 1:
            assert "image_view" in {tool.name for tool in tools}
            return ModelTurn(
                "",
                (FunctionCall("call-image", "image_view", {"path": "sample.png"}),),
            )
        return ModelTurn("The image is blue.", ())


def test_agent_session_sends_local_image_pixels_as_function_output(tmp_path: Path) -> None:
    # Given a local PNG and a model that asks to inspect it
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (4, 6), (10, 20, 200)).save(image_path, format="PNG")
    approval = AllowApproval()
    model = ImageRequestModel()
    session = AgentSession(
        model,
        default_registry(),
        ToolContext(tmp_path, approval, ()),
    )

    # When the interactive Agent handles the image request
    answer = session.ask("Describe sample.png")

    # Then the next Responses request receives verified image pixels, not a text-only path
    assert answer == "The image is blue."
    function_output = model.requests[1][-1]
    assert function_output["type"] == "function_call_output"
    content = function_output["output"]
    assert isinstance(content, list)
    text_content = content[0]
    image_content = content[1]
    assert isinstance(text_content, dict)
    assert isinstance(image_content, dict)
    assert text_content["type"] == "input_text"
    assert image_content["type"] == "input_image"
    image_url = str(image_content["image_url"])
    assert image_url.startswith("data:image/png;base64,")
    assert base64.b64decode(image_url.partition(",")[2]) == image_path.read_bytes()
    assert approval.actions == [("image_view", str(image_path))]


def test_image_view_accepts_an_explicit_absolute_path_outside_workspace(tmp_path: Path) -> None:
    # Given an image outside the selected workspace
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    image_path = tmp_path / "outside.webp"
    Image.new("RGB", (3, 5), (200, 30, 40)).save(image_path, format="WEBP")
    approval = AllowApproval()

    # When the model explicitly requests its absolute path
    result = ImageViewTool().execute(
        {"path": str(image_path)},
        ToolContext(workspace, approval, ()),
    )

    # Then the approved external image is available as model image content
    assert result.ok
    assert result.model_output[1].type == "input_image"
    assert approval.actions == [("image_view", str(image_path))]


def test_image_view_does_not_read_pixels_when_approval_is_denied(tmp_path: Path) -> None:
    # Given a valid local image with a deny-by-default approval boundary
    image_path = tmp_path / "private.png"
    Image.new("RGB", (2, 2), (1, 2, 3)).save(image_path, format="PNG")

    # When the model requests the image
    result = ImageViewTool().execute(
        {"path": image_path.name},
        ToolContext(tmp_path, DenyApproval(), ()),
    )

    # Then no image bytes cross the model boundary
    assert result.error_code == "approval_denied"
    assert result.model_output == ()


def test_image_view_rejects_non_image_content(tmp_path: Path) -> None:
    # Given a file whose extension claims PNG but whose bytes are text
    image_path = tmp_path / "fake.png"
    _ = image_path.write_text("not an image", encoding="utf-8")

    # When the model requests the invalid file
    result = ImageViewTool().execute(
        {"path": image_path.name},
        ToolContext(tmp_path, AllowApproval(), ()),
    )

    # Then the tool fails without constructing model image content
    assert result.error_code == "image_invalid"
    assert result.model_output == ()
