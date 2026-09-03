"""Channel adapters for the single on-premises Marketing Agent Service."""

from ads_booster.marketing.channels.base import ChannelApplicationAdapter
from ads_booster.marketing.channels.contracts import (
    ChannelApprovalRequest,
    ChannelIdentityBinding,
    ChannelInstallation,
    ChannelKind,
    ChannelNotification,
    ChannelResponse,
    ChannelRunRequest,
)
from ads_booster.marketing.channels.kakao import (
    KakaoChannelAdapter,
    KakaoWebhookEnvelope,
    WebApprovalLinkIssuer,
)
from ads_booster.marketing.channels.slack import (
    SlackChannelAdapter,
    SlackRequestVerifier,
    SlackWebhookEnvelope,
)
from ads_booster.marketing.channels.store import SqliteChannelStore
from ads_booster.marketing.channels.web import WebChannelAdapter

__all__ = [
    "ChannelApplicationAdapter",
    "ChannelApprovalRequest",
    "ChannelIdentityBinding",
    "ChannelInstallation",
    "ChannelKind",
    "ChannelNotification",
    "ChannelResponse",
    "ChannelRunRequest",
    "KakaoChannelAdapter",
    "KakaoWebhookEnvelope",
    "SlackChannelAdapter",
    "SlackRequestVerifier",
    "SlackWebhookEnvelope",
    "SqliteChannelStore",
    "WebApprovalLinkIssuer",
    "WebChannelAdapter",
]
