from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Final, override

from PIL import Image, UnidentifiedImageError
from pydantic import TypeAdapter

from ads_booster.agent.runs import ConnectorContextError
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.generation import GenerationReferenceImage

_MAX_REFERENCE_BYTES: Final = 16 * 1024 * 1024
_UNAVAILABLE: Final = "trace_reference_unavailable"
_SIZE_INVALID: Final = "trace_reference_size_invalid"
_DIGEST_MISMATCH: Final = "trace_reference_digest_mismatch"
_IMAGE_INVALID: Final = "trace_reference_image_invalid"
_MEDIA_MISMATCH: Final = "trace_reference_media_mismatch"
_FORMAT_BY_MEDIA_TYPE: Final = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class TraceReferenceError(ConnectorContextError):
    code: str
    reference_id: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.reference_id}"


def reference_context_messages(
    root: Path,
    references: tuple[GenerationReferenceImage, ...],
) -> tuple[JsonObject, ...]:
    resolved_root = root.resolve()
    messages: list[JsonObject] = []
    for reference in references:
        path = (resolved_root / reference.relative_path).resolve()
        if not path.is_relative_to(resolved_root) or not path.is_file():
            raise TraceReferenceError(_UNAVAILABLE, reference.reference_id)
        try:
            content = path.read_bytes()
        except OSError as error:
            raise TraceReferenceError(
                _UNAVAILABLE,
                reference.reference_id,
            ) from error
        if not content or len(content) > _MAX_REFERENCE_BYTES:
            raise TraceReferenceError(_SIZE_INVALID, reference.reference_id)
        if sha256(content).hexdigest() != reference.sha256:
            raise TraceReferenceError(_DIGEST_MISMATCH, reference.reference_id)
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
                image_format = image.format
        except (OSError, UnidentifiedImageError) as error:
            raise TraceReferenceError(_IMAGE_INVALID, reference.reference_id) from error
        if image_format != _FORMAT_BY_MEDIA_TYPE[reference.media_type]:
            raise TraceReferenceError(_MEDIA_MISMATCH, reference.reference_id)
        label = json.dumps(
            {"reference_id": reference.reference_id, "sha256": reference.sha256},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        messages.append(
            _JSON_OBJECT.validate_python(
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": label},
                        {
                            "type": "input_image",
                            "image_url": (
                                f"data:{reference.media_type};base64,"
                                f"{base64.b64encode(content).decode('ascii')}"
                            ),
                        },
                    ],
                }
            )
        )
    return tuple(messages)
