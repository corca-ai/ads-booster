from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ads_booster.capture.capture_safety import (
    CaptureAdapterError,
    path_has_symlink_component,
)
from ads_booster.contracts import ErrorCode

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject

_PRIVATE_FILE_MODE: Final = 0o600
_IMAGE_PATH_MAX_LENGTH: Final = 4_096
_UI_TOP_FRACTION: Final = 0.30
_UI_BOTTOM_FRACTION: Final = 0.84
_SOURCE_UNAVAILABLE: Final = "native Trace image is unavailable or symlinked"
_DESTINATION_UNAVAILABLE: Final = "ImageGen destination is unavailable or symlinked"
_DESTINATION_NOT_FILE: Final = "ImageGen destination is not a regular file"
_DESTINATION_SAME_AS_SOURCE: Final = "ImageGen destination must differ from native Trace image"
_SOURCE_NOT_PNG: Final = "native Trace image is not a PNG"
_SOURCE_OPEN_FAILED: Final = "native Trace image could not be opened"
_GENERATED_ROOT_INVALID: Final = "ImageGen returned a path outside its generated image root"
_GENERATED_SYMLINK: Final = "ImageGen returned a symlinked image path"
_GENERATED_PATH_INVALID: Final = "ImageGen returned an invalid image path"
_GENERATED_NOT_PNG: Final = "ImageGen output is not a PNG"
_NORMALIZE_FAILED: Final = "ImageGen PNG could not be normalized"


class ImageEditResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)

    image_path: str = Field(min_length=1, max_length=_IMAGE_PATH_MAX_LENGTH)


def validate_source_and_destination(
    source: Path,
    destination: Path,
) -> tuple[Path, Path, tuple[int, int]]:
    try:
        source_path = source.expanduser().resolve(strict=True)
        destination_path = destination.expanduser()
        if not source_path.is_file() or path_has_symlink_component(source):
            raise _image_edit_error(_SOURCE_UNAVAILABLE)
        if (
            not destination_path.is_absolute()
            or path_has_symlink_component(destination_path)
            or not destination_path.parent.is_dir()
        ):
            raise _image_edit_error(_DESTINATION_UNAVAILABLE)
        if destination_path.exists() and not destination_path.is_file():
            raise _image_edit_error(_DESTINATION_NOT_FILE)
        if source_path == destination_path.resolve(strict=False):
            raise _image_edit_error(_DESTINATION_SAME_AS_SOURCE)
        with Image.open(source_path) as source_image:
            _ = source_image.load()
            if source_image.format != "PNG":
                raise _image_edit_error(_SOURCE_NOT_PNG)
            source_size = source_image.size
    except CaptureAdapterError:
        raise
    except (OSError, ValueError) as error:
        raise _image_edit_error(_SOURCE_OPEN_FAILED) from error
    return source_path, destination_path, source_size


def parse_generated_image_path(raw_result: JsonObject, root: Path) -> Path:
    try:
        result = ImageEditResult.model_validate(raw_result)
        root_path = root.resolve(strict=False)
        candidate = Path(result.image_path).expanduser()
        if not root_path.is_dir() or not candidate.is_absolute():
            raise _image_edit_error(_GENERATED_ROOT_INVALID)
        if path_has_symlink_component(root) or path_has_symlink_component(candidate):
            raise _image_edit_error(_GENERATED_SYMLINK)
        candidate_path = candidate.resolve(strict=True)
        if not candidate_path.is_relative_to(root_path) or not candidate_path.is_file():
            raise _image_edit_error(_GENERATED_ROOT_INVALID)
    except CaptureAdapterError:
        raise
    except (OSError, RuntimeError, ValidationError) as error:
        raise _image_edit_error(_GENERATED_PATH_INVALID) from error
    else:
        return candidate_path


def write_normalized_png(
    generated: Path,
    source: Path,
    destination: Path,
    source_size: tuple[int, int],
) -> None:
    temporary: Path | None = None
    try:
        with Image.open(generated) as generated_image, Image.open(source) as source_image:
            _ = generated_image.load()
            _ = source_image.load()
            if generated_image.format != "PNG":
                raise _image_edit_error(_GENERATED_NOT_PNG)
            normalized_generated = generated_image.resize(
                source_size,
                Image.Resampling.LANCZOS,
            ).convert("RGBA")
            source_layer = source_image.convert("RGBA")
            mask = Image.new("L", source_size, 0)
            mask.paste(255, (0, 0, source_size[0], int(source_size[1] * _UI_TOP_FRACTION)))
            mask.paste(255, (0, int(source_size[1] * _UI_BOTTOM_FRACTION), *source_size))
            normalized = Image.composite(normalized_generated, source_layer, mask).convert("RGB")
            try:
                with tempfile.NamedTemporaryFile(
                    dir=destination.parent,
                    prefix=f".{destination.name}.",
                    suffix=".png",
                    delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                normalized.save(temporary, format="PNG")
                temporary.chmod(_PRIVATE_FILE_MODE)
                _ = temporary.replace(destination)
                temporary = None
            finally:
                normalized.close()
                normalized_generated.close()
                source_layer.close()
                mask.close()
    except CaptureAdapterError:
        raise
    except (OSError, ValueError) as error:
        raise _image_edit_error(_NORMALIZE_FAILED) from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _image_edit_error(message: str) -> CaptureAdapterError:
    return CaptureAdapterError(code=ErrorCode.SCENE_CAPTURE_FAILED, message=message)
