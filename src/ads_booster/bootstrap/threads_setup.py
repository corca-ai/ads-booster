from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from ads_booster.agent.core.registry import ToolRegistration, ToolRegistry
from ads_booster.channels.slack_integration_actors import SlackIntegrationActors
from ads_booster.learning.provider_metrics import ProviderMetricRepository
from ads_booster.providers.threads_api import ThreadsApiClient
from ads_booster.threads.accounts import ThreadsAccountRepository, ThreadsTokenVault
from ads_booster.threads.drafts import ThreadsDraftRepository
from ads_booster.threads.media_delivery import ThreadsMediaDelivery
from ads_booster.threads.metrics import ThreadsMetricsService
from ads_booster.threads.oauth import ThreadsOAuthService
from ads_booster.threads.publications import ThreadsPublicationRepository, ThreadsPublisher
from ads_booster.threads.reconciliation import ThreadsReconciliationRuntime
from ads_booster.tools.compatibility import DelegatingToolAdapter
from ads_booster.tools.threads_accounts import CAPABILITY as PROFILE_CAPABILITY
from ads_booster.tools.threads_accounts import ThreadsAccountProfileTool
from ads_booster.tools.threads_accounts import descriptor as profile_descriptor
from ads_booster.tools.threads_connection import CAPABILITY as CONNECT_CAPABILITY
from ads_booster.tools.threads_connection import ThreadsConnectTool
from ads_booster.tools.threads_connection import descriptor as connect_descriptor
from ads_booster.tools.threads_drafts import CAPABILITY as DRAFT_CAPABILITY
from ads_booster.tools.threads_drafts import ThreadsDraftTool
from ads_booster.tools.threads_drafts import descriptor as draft_descriptor
from ads_booster.tools.threads_tools import ThreadsTools, descriptors

if TYPE_CHECKING:
    from ads_booster.agent.service.application import MarketingAgentService
    from ads_booster.channels.slack_events import SlackEvents
    from ads_booster.contracts.tool_capability import ToolDescriptor


@dataclass(frozen=True, slots=True)
class ThreadsConfig:
    app_id: str
    app_secret: str
    redirect_uri: str
    public_origin: str
    media_signing_secret: str

    def __post_init__(self) -> None:
        origin = urlsplit(self.public_origin)
        redirect = urlsplit(self.redirect_uri)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.path not in {"", "/"}
            or bool(origin.query)
            or bool(origin.fragment)
            or self.redirect_uri
            != self.public_origin.rstrip("/") + "/integrations/threads/callback"
            or redirect.hostname != origin.hostname
            or len(self.media_signing_secret.encode()) < 32
        ):
            raise ValueError("threads_configuration_invalid")


@dataclass(slots=True)
class InstalledThreads:
    api: ThreadsApiClient
    oauth: ThreadsOAuthService
    media: ThreadsMediaDelivery
    tools: ThreadsTools
    reconciliation: ThreadsReconciliationRuntime

    def close(self) -> None:
        self.api.close()


@dataclass(frozen=True, slots=True)
class ThreadsCatalog:
    tools: ThreadsTools
    connect: ThreadsConnectTool
    drafts: ThreadsDraftTool
    profiles: ThreadsAccountProfileTool

    def registrations(self) -> tuple[ToolRegistration, ...]:
        registrations: list[ToolRegistration] = [
            ToolRegistration(
                capability_id=CONNECT_CAPABILITY,
                version="1",
                adapter=DelegatingToolAdapter(
                    capability_id=CONNECT_CAPABILITY,
                    version="1",
                    executor_id="threads-oauth",
                    executor=self.connect.execute,
                ),
                descriptor_factory=connect_descriptor,
            ),
            ToolRegistration(
                capability_id=DRAFT_CAPABILITY,
                version="1",
                adapter=DelegatingToolAdapter(
                    capability_id=DRAFT_CAPABILITY,
                    version="1",
                    executor_id="threads-draft",
                    executor=self.drafts.execute,
                ),
                descriptor_factory=draft_descriptor,
            ),
            ToolRegistration(
                capability_id=PROFILE_CAPABILITY,
                version="1",
                adapter=DelegatingToolAdapter(
                    capability_id=PROFILE_CAPABILITY,
                    version="1",
                    executor_id="threads-account-profile",
                    executor=self.profiles.execute,
                ),
                descriptor_factory=profile_descriptor,
            ),
        ]
        for descriptor in descriptors(now=datetime.now(UTC)):
            registrations.append(
                ToolRegistration(
                    capability_id=descriptor.capability_id,
                    version=descriptor.version,
                    adapter=DelegatingToolAdapter(
                        capability_id=descriptor.capability_id,
                        version=descriptor.version,
                        executor_id="threads-api",
                        executor=self.tools.execute,
                    ),
                    descriptor_factory=self._descriptor(descriptor.capability_id),
                )
            )
        return tuple(registrations)

    @staticmethod
    def _descriptor(capability_id: str) -> Callable[..., ToolDescriptor]:
        def factory(*, now: datetime) -> ToolDescriptor:
            return next(item for item in descriptors(now=now) if item.capability_id == capability_id)

        return factory

    def descriptors(self, *, now: datetime) -> tuple[ToolDescriptor, ...]:
        return ToolRegistry.from_registrations(self.registrations(), now=now).descriptors


def connect_threads(
    service: MarketingAgentService,
    events: SlackEvents,
    config: ThreadsConfig,
    *,
    now: datetime,
) -> InstalledThreads:
    database = service.repository.database_path
    root = database.parent
    api = ThreadsApiClient.create(app_id=config.app_id, app_secret=config.app_secret)
    accounts = ThreadsAccountRepository(database)
    tokens = ThreadsTokenVault(root / "secrets" / "threads")
    drafts = ThreadsDraftRepository(database)
    media = ThreadsMediaDelivery(
        database,
        root / "artifacts",
        config.public_origin,
        config.media_signing_secret.encode(),
    )
    publications = ThreadsPublicationRepository(database)
    publisher = ThreadsPublisher(api, accounts, tokens, drafts, media, publications)
    metrics = ThreadsMetricsService(api, accounts, tokens, ProviderMetricRepository(database))
    actors = SlackIntegrationActors(str(database), events.identity)
    oauth = ThreadsOAuthService(
        str(database), config.redirect_uri, api, accounts, tokens
    )
    tools = ThreadsTools(accounts, tokens, metrics, publisher, actors.threads_actor)
    service.approval_authorizers = (
        *service.approval_authorizers,
        tools.approval_allowed,
    )
    service.install_tool_catalog(
        ThreadsCatalog(
            tools,
            ThreadsConnectTool(oauth, actors.threads_actor),
            ThreadsDraftTool(drafts, accounts, actors.threads_actor, publications),
            ThreadsAccountProfileTool(accounts, actors.threads_actor, oauth),
        ),
        now=now,
    )
    return InstalledThreads(
        api,
        oauth,
        media,
        tools,
        ThreadsReconciliationRuntime(service, publisher, publications),
    )


def threads_config_from_env(env: Mapping[str, str]) -> ThreadsConfig | None:
    integration_names = (
        "TRACE_MARKETING_THREADS_APP_ID",
        "TRACE_MARKETING_THREADS_APP_SECRET",
        "TRACE_MARKETING_THREADS_REDIRECT_URI",
        "TRACE_MARKETING_THREADS_MEDIA_SECRET",
    )
    values = tuple(env.get(name) for name in integration_names)
    if all(value is None for value in values):
        return None
    names = (
        integration_names[0],
        integration_names[1],
        integration_names[2],
        "TRACE_MARKETING_PUBLIC_ORIGIN",
        integration_names[3],
    )
    if any(value is None or not value for value in (env.get(name) for name in names)):
        raise ValueError("threads_configuration_incomplete")
    return ThreadsConfig(*(_required_env(env, name) for name in names))


def _required_env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if value is None or not value:
        raise ValueError("threads_configuration_incomplete")
    return value


__all__ = [
    "InstalledThreads",
    "ThreadsCatalog",
    "ThreadsConfig",
    "connect_threads",
    "threads_config_from_env",
]
