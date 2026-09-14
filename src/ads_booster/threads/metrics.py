from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.provider_metrics import (
    ProviderMetricAvailability,
    ProviderMetricSnapshot,
)
from ads_booster.learning.provider_metrics import ProviderMetricRepository
from ads_booster.providers.threads_api import ThreadsApiClient, ThreadsApiError
from ads_booster.threads.accounts import ThreadsAccountRepository, ThreadsTokenVault
from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class ThreadsMetricsService:
    api: ThreadsApiClient
    accounts: ThreadsAccountRepository
    tokens: ThreadsTokenVault
    metrics: ProviderMetricRepository

    def collect(
        self,
        *,
        workspace_id: str,
        connection_id: str,
        subject_kind: Literal["account", "post"],
        subject_id: str,
        metric_names: tuple[str, ...],
        observed_at: datetime,
    ) -> tuple[ProviderMetricSnapshot, ...]:
        if observed_at.tzinfo is None or observed_at.utcoffset() != UTC.utcoffset(observed_at):
            raise ValueError("threads_metric_collection_requires_utc")
        account = self.accounts.require_readable(workspace_id, connection_id, now=observed_at)
        if subject_kind == "account" and subject_id != account.provider_account_id:
            raise ValueError("threads_metric_account_identity_mismatch")
        token = self.tokens.get(account.token_ref)
        try:
            if subject_kind == "account":
                observed = self.api.account_insights(token, metrics=metric_names)
            else:
                observed = self.api.insights(token, subject_id=subject_id, metrics=metric_names)
        except ThreadsApiError as error:
            if error.status == 401:
                _ = self.accounts.mark_reauth(account, now=observed_at)
            raise
        snapshots: list[ProviderMetricSnapshot] = []
        observed_by_name = {item.name: item for item in observed}
        for metric_name in metric_names:
            item = observed_by_name.get(metric_name)
            period = "unknown" if item is None else item.period
            value = None if item is None else item.value
            available = item is not None and item.available
            source: JsonObject = {
                "provider": "threads",
                "workspace_id": workspace_id,
                "connection_id": connection_id,
                "provider_account_id": account.provider_account_id,
                "subject_kind": subject_kind,
                "subject_id": subject_id,
                "metric": metric_name,
                "period": period,
                "value": value,
                "observed_at": observed_at.isoformat(),
            }
            digest = contract_sha256(source)
            snapshot = ProviderMetricSnapshot(
                snapshot_id="metric-" + sha256(digest.encode()).hexdigest()[:24],
                provider="threads",
                workspace_id=workspace_id,
                connection_id=connection_id,
                provider_account_id=account.provider_account_id,
                subject_kind=subject_kind,
                subject_id=subject_id,
                metric=metric_name,
                period=period,
                value=value,
                availability=(
                    ProviderMetricAvailability.AVAILABLE
                    if available
                    else ProviderMetricAvailability.UNAVAILABLE
                ),
                observed_at=observed_at,
                provider_api_version="v1.0",
                source_sha256=digest,
            )
            snapshots.append(self.metrics.append(snapshot))
        return tuple(snapshots)


__all__ = ["ThreadsMetricsService"]
