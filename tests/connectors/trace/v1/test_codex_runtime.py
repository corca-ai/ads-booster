from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ads_booster.connectors.trace.v1.codex_runtime import (
    CodexTraceRunner,
    CodexTraceRunStore,
)
from ads_booster.connectors.trace.v1.scene_plan import recipe_for_wallpaper_plan
from ads_booster.contracts.run import TraceRunErrorCode, TraceRunState
from ads_booster.providers.codex_cli import CodexCliError
from tests.connectors.trace.v1.test_connector import (
    RecordingRunner,
    bundle,
    completed_result,
    plan,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.generation import MarketingContextBundle
    from ads_booster.contracts.wallpaper import WallpaperPlan


@dataclass(slots=True)
class RecordingPlans:
    value: WallpaperPlan
    calls: list[MarketingContextBundle] = field(default_factory=list)

    def plan(self, bundle: MarketingContextBundle) -> WallpaperPlan:
        self.calls.append(bundle)
        return self.value


@dataclass(frozen=True, slots=True)
class RunnerFactory:
    runner: RecordingRunner

    def __call__(self, bundle: MarketingContextBundle) -> RecordingRunner:
        del bundle
        return self.runner


@dataclass(slots=True)
class FailingPlans:
    calls: int = 0

    def plan(self, bundle: MarketingContextBundle) -> WallpaperPlan:
        del bundle
        self.calls += 1
        message = "codex_exec_failed:1"
        raise CodexCliError(message)


@dataclass(slots=True)
class FlakyPlans:
    value: WallpaperPlan
    failures_remaining: int
    calls: int = 0

    def plan(self, bundle: MarketingContextBundle) -> WallpaperPlan:
        del bundle
        self.calls += 1
        if self.failures_remaining:
            self.failures_remaining -= 1
            message = "codex_exec_failed:1"
            raise CodexCliError(message)
        return self.value


def test_codex_runner_persists_validated_plan_and_reuses_completed_result(tmp_path: Path) -> None:
    plans = RecordingPlans(plan())
    native = RecordingRunner(completed_result())
    runner = CodexTraceRunner(
        plans=plans,
        trace_runners=RunnerFactory(native),
        store=CodexTraceRunStore(tmp_path / "codex-runs"),
        tool_home=tmp_path,
    )

    first = runner.run(bundle())
    replay = runner.run(bundle())

    assert first.state is TraceRunState.COMPLETED
    assert replay == first
    assert len(plans.calls) == 1
    assert len(native.calls) == 1
    assert (tmp_path / "codex-runs" / "dynamic-scene" / "plan.json").is_file()
    assert (tmp_path / "codex-runs" / "dynamic-scene" / "result.json").is_file()


def test_codex_runner_confirms_the_remote_barrier_before_appium(tmp_path: Path) -> None:
    native = RecordingRunner(completed_result())
    barriers: list[str] = []

    def confirm(task_id: str) -> None:
        assert native.calls == []
        barriers.append(task_id)

    runner = CodexTraceRunner(
        plans=RecordingPlans(plan()),
        trace_runners=RunnerFactory(native),
        store=CodexTraceRunStore(tmp_path / "codex-runs"),
        tool_home=tmp_path,
        before_side_effect=confirm,
    )

    result = runner.run(bundle())

    assert result.state is TraceRunState.COMPLETED
    assert barriers == ["dynamic-scene"]
    assert len(native.calls) == 1


def test_codex_runner_does_not_repeat_an_uncertain_appium_side_effect(tmp_path: Path) -> None:
    store = CodexTraceRunStore(tmp_path / "codex-runs")
    run_root = store.admit(bundle())
    store.save_plan(run_root, plan())
    store.mark_executing(run_root)
    native = RecordingRunner(completed_result())
    runner = CodexTraceRunner(
        plans=RecordingPlans(plan()),
        trace_runners=RunnerFactory(native),
        store=store,
        tool_home=tmp_path,
    )

    result = runner.run(bundle())

    assert result.state is TraceRunState.UNKNOWN_SIDE_EFFECT
    assert native.calls == []


def test_hosted_textual_reference_ids_are_valid_plan_authority() -> None:
    context = bundle()
    context = context.model_copy(
        update={
            "promotion_material": context.promotion_material.model_copy(
                update={"reference_ids": ("kr-020",)},
            ),
        },
    )
    planned = plan().model_copy(update={"reference_ids": ("kr-020",)})

    recipe = recipe_for_wallpaper_plan(planned, context)

    assert recipe.wallpaper_plan == planned


def test_codex_failure_becomes_a_terminal_result_without_stopping_worker(tmp_path: Path) -> None:
    native = RecordingRunner(completed_result())
    plans = FailingPlans()
    barriers: list[str] = []
    runner = CodexTraceRunner(
        plans=plans,
        trace_runners=RunnerFactory(native),
        store=CodexTraceRunStore(tmp_path / "codex-runs"),
        tool_home=tmp_path,
        before_side_effect=barriers.append,
    )

    result = runner.run(bundle())
    replay = runner.run(bundle())

    assert result.state is TraceRunState.FAILED
    assert replay == result
    assert result.failure is not None
    assert result.failure.code is TraceRunErrorCode.CODEX_PLAN_FAILED
    assert plans.calls == 3
    assert barriers == []
    assert native.calls == []
    assert (tmp_path / "codex-runs" / "dynamic-scene" / "result.json").is_file()


def test_codex_runner_retries_a_transient_plan_before_appium(tmp_path: Path) -> None:
    native = RecordingRunner(completed_result())
    plans = FlakyPlans(plan(), failures_remaining=2)
    barriers: list[str] = []
    runner = CodexTraceRunner(
        plans=plans,
        trace_runners=RunnerFactory(native),
        store=CodexTraceRunStore(tmp_path / "codex-runs"),
        tool_home=tmp_path,
        before_side_effect=barriers.append,
    )

    result = runner.run(bundle())

    assert result.state is TraceRunState.COMPLETED
    assert result.failure is None
    assert plans.calls == 3
    assert barriers == ["dynamic-scene"]
    assert len(native.calls) == 1


def test_codex_runner_retries_a_bundle_invalid_plan_before_appium(tmp_path: Path) -> None:
    native = RecordingRunner(completed_result())
    invalid_plan = plan().model_copy(update={"request_id": "different-request"})
    plans = RecordingPlans(invalid_plan)
    barriers: list[str] = []
    runner = CodexTraceRunner(
        plans=plans,
        trace_runners=RunnerFactory(native),
        store=CodexTraceRunStore(tmp_path / "codex-runs"),
        tool_home=tmp_path,
        before_side_effect=barriers.append,
    )

    result = runner.run(bundle())

    assert result.state is TraceRunState.FAILED
    assert result.failure is not None
    assert result.failure.code is TraceRunErrorCode.CODEX_PLAN_FAILED
    assert len(plans.calls) == 3
    assert barriers == []
    assert native.calls == []
    assert not (tmp_path / "codex-runs" / "dynamic-scene" / "plan.json").exists()
