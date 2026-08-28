from __future__ import annotations

import json
import os
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Final, Protocol

from pydantic import ValidationError

from ads_booster.capture.factory import build_wallpaper_capture_adapter
from ads_booster.capture.readiness import DefaultCaptureReadiness
from ads_booster.connectors.trace.v1.references import (
    TraceReferenceError,
    reference_context_messages,
)
from ads_booster.connectors.trace.v1.scene_plan import (
    TraceScenePlanError,
    recipe_for_wallpaper_plan,
)
from ads_booster.connectors.trace.v1.tools import (
    TraceGenerateMarketingImageTool,
    TracePlannedImageRunner,
)
from ads_booster.contracts.results import TraceRunResult
from ads_booster.contracts.run import TraceRunErrorCode, TraceRunFailure, TraceRunState
from ads_booster.contracts.wallpaper import WallpaperPlan
from ads_booster.providers.codex_cli import CodexCli, CodexCliError, resolve_codex_executable
from ads_booster.runtime.generate_one import GenerateOneOptions, GenerateOneRunner
from ads_booster.search.image.background import ImageSearchBackgroundFetcher
from ads_booster.search.image.providers import create_image_search_provider
from ads_booster.tools.models import ToolContext

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from ads_booster.contracts.generation import MarketingContextBundle
    from ads_booster.runtime.generate_one import BackgroundFetcher
    from ads_booster.transport.http import HttpClient
    from ads_booster.transport.json_types import JsonObject

_DEFAULT_APPIUM_SERVER: Final = "http://127.0.0.1:4723"
_PLAN_ATTEMPTS: Final = 3
_COUNTRY_TIME_ZONES: Final = {
    "BR": "America/Sao_Paulo",
    "DE": "Europe/Berlin",
    "FR": "Europe/Paris",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "TW": "Asia/Taipei",
    "US": "America/New_York",
}


class WallpaperPlanSource(Protocol):
    def plan(self, bundle: MarketingContextBundle) -> WallpaperPlan: ...


class WallpaperPlanClient(Protocol):
    def generate_json(
        self,
        prompt: str,
        schema: Mapping[str, object],
        *,
        images: tuple[Path, ...] = (),
    ) -> JsonObject: ...


class TraceRunnerFactory(Protocol):
    def __call__(self, bundle: MarketingContextBundle) -> TracePlannedImageRunner: ...


@dataclass(frozen=True, slots=True)
class CodexWallpaperPlanner:
    client: WallpaperPlanClient
    reference_root: Path

    def plan(self, bundle: MarketingContextBundle) -> WallpaperPlan:
        images = self._reference_paths(bundle)
        payload = self.client.generate_json(
            _planning_prompt(bundle),
            WallpaperPlan.model_json_schema(),
            images=images,
        )
        try:
            return WallpaperPlan.model_validate_json(json.dumps(payload))
        except ValidationError as error:
            message = "codex_wallpaper_plan_invalid"
            raise CodexCliError(message) from error

    def _reference_paths(self, bundle: MarketingContextBundle) -> tuple[Path, ...]:
        if not bundle.reference_images:
            return ()
        _ = reference_context_messages(self.reference_root, bundle.reference_images)
        root = self.reference_root.resolve()
        return tuple(
            (root / reference.relative_path).resolve() for reference in bundle.reference_images
        )


@dataclass(frozen=True, slots=True)
class CodexTraceRunStore:
    root: Path

    def input_digest(self, bundle: MarketingContextBundle) -> str:
        return sha256(bundle.model_dump_json().encode()).hexdigest()

    def admit(self, bundle: MarketingContextBundle) -> Path:
        run_root = self.root / bundle.request_id
        run_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        digest_path = run_root / "input.sha256"
        digest = self.input_digest(bundle)
        if digest_path.is_file():
            try:
                existing = digest_path.read_text(encoding="utf-8").strip()
            except OSError as error:
                message = "codex_run_state_unavailable"
                raise CodexCliError(message) from error
            if existing != digest:
                message = "codex_run_input_changed"
                raise CodexCliError(message)
        else:
            _atomic_text(digest_path, f"{digest}\n")
        return run_root

    def load_plan(self, run_root: Path) -> WallpaperPlan | None:
        return _load_model(run_root / "plan.json", WallpaperPlan)

    def save_plan(self, run_root: Path, plan: WallpaperPlan) -> None:
        _atomic_text(run_root / "plan.json", plan.model_dump_json())

    def load_result(self, run_root: Path) -> TraceRunResult | None:
        return _load_model(run_root / "result.json", TraceRunResult)

    def save_result(self, run_root: Path, result: TraceRunResult) -> None:
        _atomic_text(run_root / "result.json", result.model_dump_json())

    def executing(self, run_root: Path) -> bool:
        return (run_root / "executing").is_file()

    def mark_executing(self, run_root: Path) -> None:
        _atomic_text(run_root / "executing", "native Appium side effect started\n")


@dataclass(frozen=True, slots=True)
class TraceV1RunnerFactory:
    options: GenerateOneOptions
    background_fetcher: BackgroundFetcher
    appium_codex: CodexCli

    def __call__(self, bundle: MarketingContextBundle) -> TracePlannedImageRunner:
        return GenerateOneRunner(
            options=self.options,
            background_fetcher=self.background_fetcher,
            capture_adapter=build_wallpaper_capture_adapter(
                device_kind=bundle.device.kind,
                appium_server=self.options.appium_server,
                readiness=self.options.capture_readiness,
                codex=self.appium_codex,
            ),
        )


@dataclass(frozen=True, slots=True)
class CodexTraceRunner:
    plans: WallpaperPlanSource
    trace_runners: TraceRunnerFactory
    store: CodexTraceRunStore
    tool_home: Path
    before_side_effect: Callable[[str], None] | None = None

    def run(self, bundle: MarketingContextBundle) -> TraceRunResult:
        run_root: Path | None = None
        try:
            run_root = self.store.admit(bundle)
            existing = self.store.load_result(run_root)
            if existing is not None:
                return existing
            if self.store.executing(run_root):
                return _terminal_result(bundle, TraceRunState.UNKNOWN_SIDE_EFFECT)
            plan = self.store.load_plan(run_root)
            if plan is None:
                try:
                    plan = _plan_with_retries(self.plans, bundle)
                except CodexCliError, TraceScenePlanError:
                    result = _terminal_result(
                        bundle,
                        TraceRunState.FAILED,
                        failure=TraceRunFailure(
                            code=TraceRunErrorCode.CODEX_PLAN_FAILED,
                            message="Codex wallpaper planning failed",
                        ),
                    )
                    self.store.save_result(run_root, result)
                    return result
                self.store.save_plan(run_root, plan)
            tool = TraceGenerateMarketingImageTool(bundle, self.trace_runners(bundle))
            if self.before_side_effect is not None:
                self.before_side_effect(bundle.request_id)
            self.store.mark_executing(run_root)
            outcome = tool.execute(
                {"plan": plan.model_dump(mode="json")},
                ToolContext(self.tool_home, _ApprovedTask(), ()),
            )
            result = (
                TraceRunResult.model_validate_json(outcome.output)
                if outcome.ok
                else _terminal_result(bundle, TraceRunState.FAILED)
            )
        except CodexCliError, TraceReferenceError, OSError, ValidationError:
            result = _terminal_result(bundle, TraceRunState.FAILED)
            if run_root is None:
                return result
        self.store.save_result(run_root, result)
        return result


@dataclass(frozen=True, slots=True)
class _ApprovedTask:
    def request(self, action: str, detail: str) -> bool:
        del action, detail
        return True


def build_codex_trace_runner(
    home: Path,
    http: HttpClient,
    *,
    before_side_effect: Callable[[str], None] | None = None,
) -> CodexTraceRunner:
    executable = resolve_codex_executable()
    if executable is None:
        message = "codex_cli_unavailable"
        raise CodexCliError(message)
    appium_server = os.environ.get("TRACE_AGENT_APPIUM_SERVER", _DEFAULT_APPIUM_SERVER)
    readiness = DefaultCaptureReadiness(appium_server=appium_server)
    options = GenerateOneOptions(
        output_root=home / "generated",
        appium_server=appium_server,
        timeout_seconds=float(os.environ.get("TRACE_AGENT_GENERATION_TIMEOUT_SECONDS", "3600")),
        capture_readiness=readiness,
    )
    codex = CodexCli(
        executable=executable,
        timeout_seconds=float(os.environ.get("TRACE_CODEX_TIMEOUT_SECONDS", "180")),
        model=os.environ.get("TRACE_CODEX_MODEL"),
    )
    return CodexTraceRunner(
        plans=CodexWallpaperPlanner(
            client=codex,
            reference_root=home,
        ),
        trace_runners=TraceV1RunnerFactory(
            options=options,
            appium_codex=codex,
            background_fetcher=ImageSearchBackgroundFetcher(
                image_search=create_image_search_provider(
                    http=http,
                    provider_name=os.environ.get("TRACE_AGENT_WEB_SEARCH_PROVIDER", "auto"),
                    timeout_seconds=float(
                        os.environ.get("TRACE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS", "30")
                    ),
                ),
                http=http,
            ),
        ),
        store=CodexTraceRunStore(home / "codex-runs"),
        tool_home=home,
        before_side_effect=before_side_effect,
    )


def _planning_prompt(bundle: MarketingContextBundle) -> str:
    time_zone = _COUNTRY_TIME_ZONES.get(bundle.persona.country, "UTC")
    return (
        "Create exactly one Trace lock-screen WallpaperPlan from the supplied marketing context. "
        "Return only the JSON object required by the output schema. Preserve request_id exactly. "
        f"Use {time_zone} as time_zone. Use every promotion_material.trace_items entry exactly "
        "once. A timed source entry is 'HH:MM title': store UTC starts_at/ends_at values whose "
        "local time in time_zone reconstructs the same HH:MM and use only the clean title. Use a "
        "reasonable one-hour duration. For an untimed entry, use an all-day event with null time "
        "fields. Make every provided reference affect the visual direction and include every "
        "supplied reference_id. Choose supported layout/style enum values, an uppercase six-digit "
        "event color, and a background query with no brands, logos, text, phone, or UI. Do not run "
        "tools or modify files; this turn only plans structured data.\n\nMARKETING_CONTEXT_JSON:\n"
        f"{bundle.model_dump_json(indent=2)}"
    )


def _plan_with_retries(
    plans: WallpaperPlanSource,
    bundle: MarketingContextBundle,
) -> WallpaperPlan:
    attempt = 0
    while True:
        try:
            plan = plans.plan(bundle)
            _ = recipe_for_wallpaper_plan(plan, bundle)
        except CodexCliError, TraceScenePlanError:
            attempt += 1
            if attempt >= _PLAN_ATTEMPTS:
                raise
        else:
            return plan


def _terminal_result(
    bundle: MarketingContextBundle,
    state: TraceRunState,
    *,
    failure: TraceRunFailure | None = None,
) -> TraceRunResult:
    return TraceRunResult(
        schema_version="trace.run-result.v2",
        run_id=bundle.request_id,
        idempotency_key=f"{bundle.request_id}-v2",
        input_digest=sha256(bundle.model_dump_json().encode()).hexdigest(),
        state=state,
        failure=failure,
    )


def _atomic_text(path: Path, content: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    _ = temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o600)
    _ = temporary.replace(path)


def _load_model[ModelT: WallpaperPlan | TraceRunResult](
    path: Path,
    model: type[ModelT],
) -> ModelT | None:
    if not path.is_file():
        return None
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        message = "codex_run_state_invalid"
        raise CodexCliError(message) from error
