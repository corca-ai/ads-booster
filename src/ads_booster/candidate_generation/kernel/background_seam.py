"""The judged background, plugged into the Trace connector's fetcher seam.

`GenerateOneRunner` asks a `BackgroundFetcher` for one background by query and writes the
artifact and its provenance; that contract is the seam and it does not change here. What
this module owns is the marriage: our judged open-web fetcher on one side, the connector's
composition types on the other. The judge itself, in `candidate_generation/`, knows nothing
about either — it is handed a persona and a destination.

Keeping the direction one-way matters. The connector composes a fetcher it is given rather
than reaching into candidate generation for one, so replacing the execution kernel cannot
drag the judge along with it.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ads_booster.candidate_generation.background_factory import JudgedBackgroundFetcherFactory
from ads_booster.connectors.trace.v1.codex_runtime import (
    TraceV1RunnerFactory as CodexRunnerFactory,
)
from ads_booster.connectors.trace.v1.codex_runtime import build_codex_trace_runner
from ads_booster.connectors.trace.v1.composition import (
    TraceV1Composition,
    TraceV1RunnerFactory,
    build_trace_v1_runner,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ads_booster.config.settings import AgentSettings
    from ads_booster.connectors.trace.v1.codex_runtime import CodexTraceRunner
    from ads_booster.connectors.trace.v1.composition import TraceV1GenerateOneRunner
    from ads_booster.runtime.generate_one import GenerateOneOptions
    from ads_booster.transport.http import HttpClient


def judged_background_fetchers(
    http: HttpClient,
    settings: AgentSettings,
) -> JudgedBackgroundFetcherFactory:
    """Return the per-bundle fetcher factory the Trace runner should use."""
    return JudgedBackgroundFetcherFactory(http=http, settings=settings)


def build_judged_trace_runner(
    home: Path,
    http: HttpClient,
    settings: AgentSettings | None = None,
    options: GenerateOneOptions | None = None,
    reference_root: Path | None = None,
) -> TraceV1GenerateOneRunner:
    """Compose the Trace v1 runner with the judged background fetcher behind its seam.

    With no `options`, the connector's own environment-derived defaults are used and only
    the fetcher is supplied. With `options`, the caller owns the output root, Appium
    server, and timeout, which is what the one-shot CLI needs.
    """
    if options is None:
        return build_trace_v1_runner(
            home,
            http,
            background_fetchers=judged_background_fetchers(http, _settings(settings, home)),
        )
    resolved = _settings(settings, home)
    return TraceV1Composition(
        home=home,
        settings=resolved,
        http=http,
        options=options,
        background_fetchers=judged_background_fetchers(http, resolved),
        reference_root=home if reference_root is None else reference_root,
    ).build()


def build_judged_codex_trace_runner(
    home: Path,
    http: HttpClient,
    settings: AgentSettings | None = None,
    *,
    before_side_effect: Callable[[str], None] | None = None,
) -> CodexTraceRunner:
    """Compose the Codex runtime with the judged background behind its fetcher seam.

    The Codex runner owns planning, the run store, and native capture; the only thing
    exchanged here is which fetcher answers "give me one background for this bundle".
    Its own factory takes a single fetcher because the stock one needs no context, while
    the judge has to know who it is choosing for, so the connector's per-bundle factory
    stands in its place. Nothing about how the Codex runtime executes is touched, which is
    what keeps this file the whole cost of replacing it.
    """
    runner = build_codex_trace_runner(home, http, before_side_effect=before_side_effect)
    stock = runner.trace_runners
    if not isinstance(stock, CodexRunnerFactory):
        message = "codex_trace_runner_factory_unrecognised"
        raise TypeError(message)
    return replace(
        runner,
        trace_runners=TraceV1RunnerFactory(
            options=stock.options,
            background_fetchers=judged_background_fetchers(http, _settings(settings, home)),
        ),
    )


def _settings(settings: AgentSettings | None, home: Path) -> AgentSettings:
    if settings is not None:
        return settings
    from ads_booster.config.settings import AgentSettings  # noqa: PLC0415

    return AgentSettings.from_environment(home)
