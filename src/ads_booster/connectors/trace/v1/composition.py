from __future__ import annotations

import os
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Final, Protocol

from pydantic import TypeAdapter

from ads_booster.agent.memory import JsonlMemoryStore
from ads_booster.agent.runs import (
    AgentGoal,
    AgentRun,
    AgentRunAlreadyExistsError,
    AgentRunId,
    AgentRunSessionBuilder,
    AgentRunState,
    AgentRunStore,
    AgentRuntime,
    AgentRunUpdate,
    ConnectorRegistry,
    ToolPolicy,
)
from ads_booster.agent.session import AgentError
from ads_booster.auth.codex import CodexOAuth, OAuthError
from ads_booster.auth.store import AuthStore
from ads_booster.capture.factory import build_wallpaper_capture_adapter
from ads_booster.capture.readiness import DefaultCaptureReadiness
from ads_booster.config.settings import AgentSettings
from ads_booster.connectors.trace.v1 import (
    TraceMarketingConnector,
)
from ads_booster.connectors.trace.v1.connector import trace_connector_manifest
from ads_booster.contracts.generation import MarketingContextBundle
from ads_booster.contracts.results import TraceRunResult
from ads_booster.contracts.run import TraceRunState
from ads_booster.providers.codex import CodexResponsesClient
from ads_booster.providers.errors import ProviderError
from ads_booster.runtime.generate_one import (
    GenerateOneOptions,
    GenerateOneRunner,
)
from ads_booster.tools.models import ToolContext
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ads_booster.connectors.trace.v1 import TracePlannedImageRunner
    from ads_booster.runtime.generate_one import BackgroundFetcher
    from ads_booster.transport.http import HttpClient

_TRACE_TOOL: Final = "trace_generate_marketing_image"
_DEFAULT_APPIUM_SERVER: Final = "http://127.0.0.1:4723"
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class TraceRunnerFactory(Protocol):
    def __call__(self, bundle: MarketingContextBundle) -> TracePlannedImageRunner: ...


class BackgroundFetcherFactory(Protocol):
    """Builds the background fetcher for one bundle.

    The fetcher is chosen per bundle rather than once per process because the judged
    open-web fetcher has to be told whose lock screen it is choosing a photo for; a factory
    is the smallest seam that carries the persona to it without changing the runner.
    """

    def __call__(self, bundle: MarketingContextBundle) -> BackgroundFetcher: ...


@dataclass(frozen=True, slots=True)
class TraceConnectorApproval:
    def request(self, action: str, detail: str) -> bool:
        del detail
        return action == _TRACE_TOOL


@dataclass(frozen=True, slots=True)
class TraceV1RunnerFactory:
    options: GenerateOneOptions
    background_fetchers: BackgroundFetcherFactory

    def __call__(self, bundle: MarketingContextBundle) -> TracePlannedImageRunner:
        return GenerateOneRunner(
            options=self.options,
            background_fetcher=self.background_fetchers(bundle),
            capture_adapter=build_wallpaper_capture_adapter(
                device_kind=bundle.device.kind,
                appium_server=self.options.appium_server,
                readiness=self.options.capture_readiness,
            ),
        )


@dataclass(frozen=True, slots=True)
class TraceV1GenerateOneRunner:
    store: AgentRunStore
    sessions: AgentRunSessionBuilder
    trace_runners: TraceRunnerFactory
    reference_root: Path | None = None
    clock: Callable[[], float] = time.time

    def run(self, bundle: MarketingContextBundle) -> TraceRunResult:
        """Run one marketing bundle through the durable Agent and Trace connector."""
        admitted = self._admit(bundle)
        admitted_bundle = MarketingContextBundle.model_validate(admitted.goal.context)
        connector = TraceMarketingConnector(
            admitted_bundle,
            self.trace_runners(admitted_bundle),
            reference_root=self.reference_root,
        )
        existing = connector.completed_result(admitted)
        if existing is not None:
            return existing
        try:
            finished = AgentRuntime(
                store=self.store,
                connectors=ConnectorRegistry((connector,)),
                sessions=self.sessions,
                clock=self.clock,
            ).run(admitted.run_id)
        except (AgentError, OAuthError, ProviderError) as error:
            self._record_failure(admitted.run_id, str(error))
            return _failed_result(admitted_bundle)
        result = connector.completed_result(finished)
        return _failed_result(admitted_bundle) if result is None else result

    def _admit(self, bundle: MarketingContextBundle) -> AgentRun:
        run_id = AgentRunId(bundle.request_id)
        context = _JSON_OBJECT.validate_python(bundle.model_dump(mode="json"))
        manifest = trace_connector_manifest()
        candidate = AgentRun(
            run_id=run_id,
            connector_id=manifest.connector_id,
            connector_version=manifest.version,
            goal=AgentGoal(
                objective=(
                    "Create one dynamic Trace marketing image and prepare it for human review"
                ),
                success_criteria=(
                    "all persona, promotion, and reference inputs influence the plan",
                    "the native Trace export is request-bound and verified",
                    "the final artifact and digest are ready for human review",
                ),
                context=context,
            ),
            tool_policy=ToolPolicy(allow=(_TRACE_TOOL,)),
        )
        try:
            return self.store.create(candidate, now=self.clock())
        except AgentRunAlreadyExistsError:
            return self.store.get(run_id)

    def _record_failure(self, run_id: AgentRunId, reason: str) -> None:
        current = self.store.get(run_id)
        if current.state is not AgentRunState.RUNNING:
            return
        _ = self.store.update(
            run_id,
            AgentRunUpdate(
                expected_revision=current.revision,
                state=AgentRunState.FAILED,
                at=self.clock(),
                terminal_reason=reason,
            ),
        )


@dataclass(frozen=True, slots=True)
class TraceV1Composition:
    home: Path
    settings: AgentSettings
    http: HttpClient
    options: GenerateOneOptions
    background_fetchers: BackgroundFetcherFactory
    reference_root: Path

    def build(self) -> TraceV1GenerateOneRunner:
        """Compose one Agent runner for CLI or service ownership."""
        memory = JsonlMemoryStore(self.home / "core-agent" / "memory.jsonl")
        return TraceV1GenerateOneRunner(
            store=AgentRunStore(self.home / "core-agent"),
            sessions=AgentRunSessionBuilder(
                settings=self.settings,
                client=CodexResponsesClient(
                    http=self.http,
                    oauth=CodexOAuth(http=self.http, store=AuthStore.default()),
                    model=self.settings.model,
                    reasoning_effort=self.settings.reasoning_effort,
                ),
                context=ToolContext(self.home, TraceConnectorApproval(), ()),
                memory_store=memory,
            ),
            trace_runners=TraceV1RunnerFactory(
                options=self.options,
                background_fetchers=self.background_fetchers,
            ),
            reference_root=self.reference_root,
        )


def build_trace_v1_runner(
    home: Path,
    http: HttpClient,
) -> TraceV1GenerateOneRunner:
    """Compose the installed service's Agent and Trace connector runtime."""
    settings = AgentSettings.from_environment(home)
    appium_server = os.environ.get("TRACE_AGENT_APPIUM_SERVER", _DEFAULT_APPIUM_SERVER)
    readiness = DefaultCaptureReadiness(appium_server=appium_server)
    options = GenerateOneOptions(
        output_root=home / "generated",
        appium_server=appium_server,
        timeout_seconds=float(os.environ.get("TRACE_AGENT_GENERATION_TIMEOUT_SECONDS", "120")),
        capture_readiness=readiness,
    )
    return TraceV1Composition(
        home=home,
        settings=settings,
        http=http,
        options=options,
        # Imported here rather than at module scope: the candidate_generation package's
        # composition root imports this module, so a top-level import would close the cycle.
        background_fetchers=_judged_background_fetchers(http, settings),
        reference_root=home,
    ).build()


def _judged_background_fetchers(
    http: HttpClient,
    settings: AgentSettings,
) -> BackgroundFetcherFactory:
    from ads_booster.candidate_generation.background_factory import (  # noqa: PLC0415
        JudgedBackgroundFetcherFactory,
    )

    return JudgedBackgroundFetcherFactory(http=http, settings=settings)


def _failed_result(bundle: MarketingContextBundle) -> TraceRunResult:
    return TraceRunResult(
        schema_version="trace.run-result.v2",
        run_id=bundle.request_id,
        idempotency_key=f"{bundle.request_id}-v2",
        input_digest=sha256(bundle.model_dump_json().encode()).hexdigest(),
        state=TraceRunState.FAILED,
    )
