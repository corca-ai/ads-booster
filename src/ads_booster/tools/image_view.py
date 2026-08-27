from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final, final, override

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ads_booster.contracts.tools import ToolDescriptor
from ads_booster.tools.models import ToolOutputImage, ToolOutputText, ToolResult
from ads_booster.tools.paths import resolve_workspace_path

if TYPE_CHECKING:
    from ads_booster.tools.models import ToolContext
    from ads_booster.transport.json_types import JsonObject

_MAX_IMAGE_BYTES: Final = 16 * 1024 * 1024
_MAX_IMAGE_PIXELS: Final = 40_000_000
_CODE_APPROVAL: Final = "approval_denied"
_CODE_DIMENSIONS: Final = "image_dimensions_invalid"
_CODE_FORMAT: Final = "image_format_unsupported"
_CODE_INVALID: Final = "image_invalid"
_CODE_READ: Final = "image_read_failed"
_CODE_SIZE: Final = "image_size_invalid"
_CODE_UNAVAILABLE: Final = "image_unavailable"
_MESSAGE_APPROVAL: Final = "image viewing was denied"
_MESSAGE_DIMENSIONS: Final = "image exceeds the 40 megapixel limit"
_MESSAGE_FORMAT: Final = "image format must be PNG, JPEG, or WebP"
_MESSAGE_SIZE: Final = "image must be between 1 byte and 16 MiB"
_MESSAGE_UNAVAILABLE: Final = "image file is unavailable"
_MEDIA_TYPE_BY_FORMAT: Final = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


class _ImageViewArgs(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    path: str = Field(min_length=1, max_length=4_096)


@dataclass(frozen=True, slots=True)
class _ImagePayload:
    content: bytes
    width: int
    height: int
    media_type: str
    digest: str


@final
class _ImageViewError(RuntimeError):
    code: str
    message: str

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(code, message)

    @override
    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ImageViewTool:
    name: ClassVar[str] = "image_view"

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description=(
                "Inspect pixels from a local PNG, JPEG, or WebP image. Relative paths resolve "
                "inside the agent workspace; explicitly supplied absolute paths are also "
                "supported. "
                "The image is sent to the selected model after approval."
            ),
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: JsonObject, context: ToolContext) -> ToolResult:
        try:
            parsed = _ImageViewArgs.model_validate(arguments)
        except ValidationError:
            return ToolResult(
                ok=False,
                output="image path is required",
                error_code="invalid_arguments",
            )
        try:
            path = _resolve_image_path(context.workspace, parsed.path)
        except _ImageViewError as error:
            return ToolResult(ok=False, output=str(error), error_code=error.code)
        if not context.approval.request(self.name, str(path)):
            return ToolResult(ok=False, output=_MESSAGE_APPROVAL, error_code=_CODE_APPROVAL)
        try:
            payload = _load_image(path)
        except _ImageViewError as error:
            return ToolResult(
                ok=False,
                output=str(error),
                error_code=error.code,
            )
        label = (
            f"Local image {path.name}: {payload.width}x{payload.height}, sha256={payload.digest}"
        )
        encoded = base64.b64encode(payload.content).decode("ascii")
        image_url = f"data:{payload.media_type};base64,{encoded}"
        return ToolResult(
            ok=True,
            output=label,
            model_output=(ToolOutputText(text=label), ToolOutputImage(image_url=image_url)),
        )


def _resolve_image_path(workspace: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        resolved = resolve_workspace_path(workspace, raw_path)
        if resolved is None or not resolved.is_file():
            raise _ImageViewError(_CODE_UNAVAILABLE, _MESSAGE_UNAVAILABLE)
        return resolved
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise _ImageViewError(_CODE_UNAVAILABLE, _MESSAGE_UNAVAILABLE) from error
    if not resolved.is_file():
        raise _ImageViewError(_CODE_UNAVAILABLE, _MESSAGE_UNAVAILABLE)
    return resolved


def _load_image(path: Path) -> _ImagePayload:
    try:
        content = path.read_bytes()
    except OSError as error:
        message = f"image could not be read: {error}"
        raise _ImageViewError(_CODE_READ, message) from error
    if not content or len(content) > _MAX_IMAGE_BYTES:
        raise _ImageViewError(_CODE_SIZE, _MESSAGE_SIZE)
    try:
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            image_format = image.format
            image.verify()
    except (OSError, UnidentifiedImageError) as error:
        message = f"image is invalid: {error}"
        raise _ImageViewError(_CODE_INVALID, message) from error
    if width * height > _MAX_IMAGE_PIXELS:
        raise _ImageViewError(_CODE_DIMENSIONS, _MESSAGE_DIMENSIONS)
    media_type = _MEDIA_TYPE_BY_FORMAT.get(image_format or "")
    if media_type is None:
        raise _ImageViewError(_CODE_FORMAT, _MESSAGE_FORMAT)
    return _ImagePayload(content, width, height, media_type, sha256(content).hexdigest())
