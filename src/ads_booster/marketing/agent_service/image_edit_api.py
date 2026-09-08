"""Authenticated local review of unknown image generation outcomes."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.agent_service.creative_image_edit import CreativeImageEditTool

if TYPE_CHECKING:
    from ads_booster.marketing.agent_service.application import MarketingAgentService
    from ads_booster.marketing.agent_service.oauth import OAuthIdentity
    from ads_booster.transport.json_types import JsonObject

_ROUTE = re.compile(
    r"^/v1/runs/([A-Za-z0-9._:-]{1,160})/image-edits/(image-edit-[a-f0-9]{48})(/abandon)?$"
)


class AbandonImageEditRequest(ContractModel):
    invocation_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    note: Annotated[str, Field(min_length=1, max_length=2000)]


def dispatch_image_edit(  # noqa: PLR0911, PLR0913 - explicit HTTP authorization boundary.
    method: str,
    path: str,
    body: bytes,
    *,
    identity: OAuthIdentity,
    service: MarketingAgentService,
    allow_review: bool = False,
) -> tuple[int, JsonObject] | None:
    match = _ROUTE.fullmatch(path)
    if match is None:
        return None
    if service.repository.get(identity.tenant_id, match[1]) is None:
        return 404, {"error": "agent_run_not_found"}
    tool = service.tools.get("creative.image.edit")
    if not isinstance(tool, CreativeImageEditTool):
        return 404, {"error": "image_edit_not_configured"}
    if method == "GET" and match[3] is None:
        return 200, tool.operation_status(identity.tenant_id, match[1], match[2])
    if method != "POST" or match[3] != "/abandon":
        return 405, {"error": "image_edit_method_not_allowed"}
    if not allow_review:
        return 403, {"error": "agent_reviewer_required"}
    request = AbandonImageEditRequest.model_validate_json(body)
    return 200, tool.abandon(
        identity.tenant_id,
        match[1],
        match[2],
        invocation_sha256=request.invocation_sha256,
        reviewer_id=identity.principal_id,
        note=request.note,
    )
