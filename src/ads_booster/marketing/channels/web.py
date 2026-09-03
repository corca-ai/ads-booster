"""Reference Web channel using the same channel-neutral Agent Service methods."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ads_booster.marketing.channels.base import ChannelApplicationAdapter
from ads_booster.marketing.channels.contracts import (
    ChannelApprovalRequest,
    ChannelIdentityBinding,
    ChannelInstallation,
    ChannelResponse,
    ChannelRunRequest,
)

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(slots=True)
class WebChannelAdapter:
    application: ChannelApplicationAdapter

    def create_run(
        self,
        installation: ChannelInstallation,
        identity: ChannelIdentityBinding,
        request: ChannelRunRequest,
        *,
        now: datetime,
    ) -> ChannelResponse:
        return self.application.create_run(installation, identity, request, now=now)

    def decide_approval(
        self,
        installation: ChannelInstallation,
        identity: ChannelIdentityBinding,
        request: ChannelApprovalRequest,
        *,
        now: datetime,
    ) -> ChannelResponse:
        return self.application.decide_approval(installation, identity, request, now=now)


__all__ = ["WebChannelAdapter"]
