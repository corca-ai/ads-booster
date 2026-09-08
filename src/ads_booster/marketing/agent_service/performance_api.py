"""Read-only, authenticated Run projection of human-reported marketing observations."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from ads_booster.contracts.agent_memory import MemoryAccess, MemoryScope
from ads_booster.marketing.agent_service.performance_observations import PerformanceObservationStore

if TYPE_CHECKING:
    from ads_booster.marketing.agent_service.application import MarketingAgentService
    from ads_booster.marketing.agent_service.oauth import OAuthIdentity
    from ads_booster.transport.json_types import JsonObject

_ROUTE = re.compile(r"^/v1/runs/([A-Za-z0-9][A-Za-z0-9._:-]{0,159})/performance$")
_MAX_OBSERVATIONS = 100


def dispatch_performance(
    method: str, target: str, *, identity: OAuthIdentity, service: MarketingAgentService
) -> tuple[int, JsonObject] | None:
    route = urlsplit(target)
    match = _ROUTE.fullmatch(route.path)
    if match is None:
        return None
    run_id = match[1]
    if service.repository.get(identity.tenant_id, run_id) is None:
        return 404, {"error": "agent_run_not_found"}
    if identity.tenant_id.startswith("slack-private-"):
        return 403, {"error": "performance_private_chat_not_projected"}
    if method != "GET":
        return 405, {"error": "performance_read_only"}
    if route.query or route.fragment:
        return 400, {"error": "performance_query_not_supported"}
    access = MemoryAccess(
        scope=MemoryScope(workspace_id=identity.tenant_id, product_id="trace", work_id=run_id),
        actor_id=identity.principal_id,
    )
    records = PerformanceObservationStore(service.repository.database_path).list(
        access, current_only=True, limit=_MAX_OBSERVATIONS
    )
    return 200, {
        "run_id": run_id,
        "evidence_status": "human_reported",
        "current_only": True,
        "limit": _MAX_OBSERVATIONS,
        "limit_reached": len(records) == _MAX_OBSERVATIONS,
        "observations": [item.model_dump(mode="json") for item in records],
        "interpretation": "Human reports; missing metrics are not zero; no causal inference.",
    }
