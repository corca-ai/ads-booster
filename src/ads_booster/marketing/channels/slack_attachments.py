"""Bounded file references from authenticated events; references are not visual proof."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ads_booster.contracts.models import ContractModel
from ads_booster.transport.json_types import JsonObject

_MAX_ATTACHMENTS = 8


class SlackAttachment(ContractModel):
    file_id: Annotated[str, Field(pattern=r"^F[A-Z0-9]{1,80}$")]
    name: Annotated[str, Field(max_length=240)] = ""
    media_type: Annotated[str, Field(max_length=120)] = ""


def attachment_references(event: JsonObject) -> tuple[SlackAttachment, ...]:
    values = event.get("files", [])
    if not isinstance(values, list) or len(values) > _MAX_ATTACHMENTS:
        raise ValueError("slack_attachments_invalid")
    result: list[SlackAttachment] = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("slack_attachment_invalid")
        result.append(
            SlackAttachment.model_validate(
                {
                    "file_id": value.get("id"),
                    "name": value.get("name", ""),
                    "media_type": value.get("mimetype", ""),
                }
            )
        )
    if len({item.file_id for item in result}) != len(result):
        raise ValueError("slack_attachment_duplicate")
    return tuple(result)
