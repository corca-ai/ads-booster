"""Run installed completion scenarios; fixture code is copied, product code must be installed."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from secrets import token_hex
from time import monotonic
from typing import TYPE_CHECKING, override

from pydantic import TypeAdapter

from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult
    from tests.marketing.agent_service.installed_completion_report import ReportValue

FIXTURE_FILES = (
    "marketing/agent_service/test_task_completion.py",
    "marketing/agent_service/test_completion_repair.py",
    "marketing/agent_service/test_completion_budget_baseline.py",
    "marketing/agent_service/test_task_drive.py",
    "marketing/agent_service/test_work_continuation.py",
    "marketing/agent_service/test_application_deferred.py",
    "marketing/agent_service/test_drive_work.py",
    "marketing/channels/test_slack_drive.py",
    "marketing/channels/test_slack_run_notifications.py",
    "marketing/agent_service/test_run_limits.py",
    "marketing/agent_service/test_failure_progress.py",
    "marketing/agent_service/test_evidence_handles.py",
    "marketing/agent_service/test_completion_assessment.py",
    "marketing/agent_service/test_deterministic_completion.py",
    "marketing/agent_service/test_completion_proof_registry.py",
    "marketing/agent_service/test_completion_regression_corpus.py",
    "marketing/agent_service/test_completion_supersession.py",
    "marketing/agent_service/test_http_api.py",
    "marketing/channels/test_task_results.py",
    "marketing/channels/test_task_attachments.py",
    "marketing/channels/test_completion_gate_fixes.py",
)
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
MODEL_CASES = ("response", "correction", "artifact", "blocked")


class Arguments(argparse.Namespace):
    mode: str = "fixture"
    scenario: str = "all"
    output_root: Path = Path()
    codex: Path = Path("/Users/park/.local/bin/codex")
    model: str = "gpt-6-astra"
    worker: str = ""
    case: str = ""
    baseline: bool = False
    baseline_python: Path | None = None
    source_root: Path | None = None


def write_json(path: Path, value: Mapping[str, ReportValue]) -> None:
    _ = path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def installed_manifest(checkout: Path) -> JsonObject:
    package = importlib.import_module("ads_booster")
    location = Path(str(package.__file__)).resolve()
    if (
        not sys.flags.isolated
        or "site-packages" not in location.parts
        or location.is_relative_to(checkout)
    ):
        message = "canary_requires_isolated_noneditable_install"
        raise RuntimeError(message)
    installed_hashes: JsonObject = {
        str(path.relative_to(location.parent)): sha256(path.read_bytes()).hexdigest()
        for path in location.parent.rglob("*.py")
    }
    sources = checkout / "src" / "ads_booster"
    source_hashes: JsonObject = {
        str(path.relative_to(sources)): sha256(path.read_bytes()).hexdigest()
        for path in sources.rglob("*.py")
    }
    return {
        "python": sys.executable,
        "pid": os.getpid(),
        "package": str(location),
        "isolated": bool(sys.flags.isolated),
        "cwd": str(Path.cwd()),
        "installed_python_sha256s": installed_hashes,
        "source_python_sha256s": source_hashes,
        "source_root": str(checkout),
        "all_python_bytes_match_source": installed_hashes == source_hashes,
    }


def run_command(command: list[str], root: Path, name: str, *, timeout: int = 600) -> int:
    started = monotonic()
    timed_out = False
    with subprocess.Popen(  # noqa: S603 - fixed argument arrays, no shell.
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.send_signal(signal.SIGINT)
            try:
                stdout, stderr = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
        code = process.wait()
    _ = (root / f"{name}.stdout.txt").write_text(stdout)
    _ = (root / f"{name}.stderr.txt").write_text(stderr)
    write_json(
        root / f"{name}.command.json",
        {
            "argv": command,
            "pid": process.pid,
            "exit_code": code,
            "exited": True,
            "timed_out": timed_out,
            "elapsed_seconds": monotonic() - started,
        },
    )
    return 124 if timed_out else code


def health_probe(args: Arguments, root: Path) -> None:
    executable = Path(sys.executable).parent / "trace-marketing"
    if run_command([str(executable), "service", "run", "--help"], root, "cli-help"):
        message = "installed_cli_help_failed"
        raise RuntimeError(message)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = TypeAdapter(tuple[str, int]).validate_python(reservation.getsockname())[1]
    command = [
        str(executable),
        "service",
        "run",
        "--model",
        args.model,
        "--home",
        str(root / "health-home"),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    environment = {key: value for key, value in os.environ.items() if not key.startswith("TRACE_")}
    environment["TRACE_MARKETING_SERVICE_TOKEN"] = token_hex(24)
    environment["PATH"] = str(args.codex.parent) + os.pathsep + environment.get("PATH", "")
    with (root / "health-server.log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=environment)  # noqa: S603 - fixed installed CLI invocation and ephemeral state root.
        try:
            status = run_command(
                [
                    "curl",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--retry",
                    "10",
                    "--retry-connrefused",
                    "--retry-delay",
                    "1",
                    f"http://127.0.0.1:{port}/health",
                ],
                root,
                "health-readback",
                timeout=30,
            )
            if status:
                message = "installed_health_failed"
                raise RuntimeError(message)
            health = _JSON.validate_json((root / "health-readback.stdout.txt").read_text())
            assert health["status"] == "ok"
            assert health["owner"] == "on_prem_agent"
            assert process.poll() is None
        finally:
            process.terminate()
            try:
                _ = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                _ = process.wait(timeout=5)
            write_json(
                root / "health-cleanup.json",
                {
                    "pid": process.pid,
                    "port": port,
                    "returncode": process.returncode,
                    "exited": process.poll() is not None,
                    "argv": command,
                },
            )


def fixture_worker(root: Path, checkout: Path, case: str = "") -> int:
    import pytest  # noqa: PLC0415 - optional fixture dependency.

    test_root = root / "fixture-suite"
    sys.path.insert(0, str(test_root))
    write_json(root / "fixture-import-before.json", installed_manifest(checkout))
    selected = (
        ("marketing/agent_service/test_completion_repair.py",)
        if case == "image-repair"
        else FIXTURE_FILES
    )
    code = pytest.main(
        [
            "-q",
            "-p",
            "no:cacheprovider",
            "--tb=short",
            "--import-mode=importlib",
            "-c",
            str(root / "empty.ini"),
            "--rootdir",
            str(test_root),
            "--basetemp",
            str(root / "fixture-artifacts"),
            *(str(test_root / "tests" / name) for name in selected),
            *(["-k", "none or long_brief"] if case == "image-repair" else []),
        ]
    )
    product_modules = {
        name: str(module.__file__)
        for name, module in sys.modules.items()
        if name.startswith("ads_booster") and getattr(module, "__file__", None)
    }
    escaped = [path for path in product_modules.values() if "site-packages" not in Path(path).parts]
    write_json(
        root / "fixture-import-after.json",
        {
            **installed_manifest(checkout),
            "modules": product_modules,
            "escaped_product_modules": escaped,
        },
    )
    return int(code) if not escaped else 1


def restart_worker(root: Path, phase: str, checkout: Path) -> None:
    sys.path.insert(0, str(root / "fixture-suite"))
    from ads_booster.bootstrap.completion_policy import load_completion_policy  # noqa: PLC0415
    from ads_booster.contracts.reasoning import ReasoningDecision  # noqa: PLC0415
    from tests.marketing.agent_service.test_application import reasoning_result  # noqa: PLC0415
    from tests.marketing.agent_service.test_task_drive import FreshResearch, Steps  # noqa: PLC0415
    from tests.marketing.channels.test_slack_commands import NOW  # noqa: PLC0415
    from tests.marketing.channels.test_slack_drive import configure  # noqa: PLC0415
    from tests.marketing.channels.test_slack_events import receive, setup_events  # noqa: PLC0415

    state = root / "restart-state"
    state.mkdir(exist_ok=True)
    owner, messages = setup_events(state)
    adapter = FreshResearch()
    default_budget = load_completion_policy({}).slack_budget

    class DefaultBudgetSteps(Steps):
        @override
        def plan(self, request: ReasoningRequest) -> ReasoningResult:
            calls = default_budget.max_tool_calls - request.remaining_tool_calls
            decision = ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool" if calls < 10 else "stop",
                capability_id="research.web" if calls < 10 else None,
                tool_input={"query": str(calls)} if calls < 10 else None,
                expected_outcome="Ten observations",
                reasoning_summary="Finished draft",
            )
            return reasoning_result(request, decision)

    configure(owner, state, DefaultBudgetSteps(), adapter)
    owner.commands.new_run_budget = default_budget
    if phase == "restart-start":
        receive(owner)
        assert owner.work_once(now=NOW)
    else:
        owner.recover()
        for _ in range(8):
            _ = owner.work_once(now=NOW)
    service = owner.commands.application.service
    runs = service.repository.list_runs("team")
    assert len(runs) == 1
    run = runs[0]
    assert run.budget.max_tool_calls == 32
    assert run.budget.max_cost_units == 50
    records = service.repository.records("team", run.run_id)
    receipts = [item.payload for item in records if item.kind.value == "receipt"]
    value = {
        **installed_manifest(checkout),
        "phase": phase,
        "run": run.model_dump(mode="json"),
        "messages": messages,
        "phase_adapter_inputs": adapter.inputs,
        "tool_receipts": receipts,
    }
    write_json(root / f"{phase}.json", value)
    if phase == "restart-start":
        assert run.state.value == "running"
    else:
        assert run.state.value == "completed"
        assert len(receipts) == 10
        assert sum(item.get("text") == "Finished draft" for item in messages) == 1


def run_worker(args: Arguments, root: Path, checkout: Path) -> int | None:
    if args.worker:
        _ = installed_manifest(args.source_root or checkout)
    if args.worker == "manifest":
        write_json(root / "provenance.json", installed_manifest(args.source_root or checkout))
        return 0
    if args.worker == "pytest":
        return fixture_worker(root, checkout, args.case)
    if args.worker.startswith("restart-"):
        restart_worker(root, args.worker, checkout)
        return 0
    if args.worker in {"reservation-crash", "reservation-restart"}:
        sys.path.insert(0, str(root / "fixture-suite"))
        from tests.marketing.agent_service.installed_completion_crash import (  # noqa: PLC0415
            reservation_crash,
        )

        reservation_crash(root, restart=args.worker == "reservation-restart")
    if args.worker.startswith("rollback-"):
        sys.path.insert(0, str(root / "fixture-suite"))
        from tests.marketing.agent_service import installed_completion_rollback  # noqa: PLC0415

        installed_completion_rollback.rollback_check(root, args.worker, args.baseline_python)
        return 0
    if args.worker == "model":
        sys.path.insert(0, str(root / "fixture-suite"))
        from tests.marketing.agent_service import installed_completion_model  # noqa: PLC0415

        installed_completion_model.run_model_case(
            args.case, root, args.codex, args.model, baseline=args.baseline
        )
        return 0
    return 0 if args.worker else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--mode", choices=("fixture", "model"), required=True)
    _ = parser.add_argument("--scenario", default="all")
    _ = parser.add_argument("--output-root", type=Path, required=True)
    _ = parser.add_argument("--codex", type=Path, default=Path("/Users/park/.local/bin/codex"))
    _ = parser.add_argument("--model", default="gpt-6-astra")
    _ = parser.add_argument(
        "--worker",
        default="",
        choices=(
            "",
            "manifest",
            "pytest",
            "restart-start",
            "restart-drain",
            "rollback-active",
            "rollback-safe",
            "reservation-crash",
            "reservation-restart",
            "model",
        ),
        help=argparse.SUPPRESS,
    )
    _ = parser.add_argument("--case", default="", help=argparse.SUPPRESS)
    _ = parser.add_argument("--baseline", action="store_true")
    _ = parser.add_argument("--baseline-python", type=Path)
    _ = parser.add_argument("--source-root", type=Path)
    args = parser.parse_args(namespace=Arguments())
    root, checkout = args.output_root.resolve(), Path(__file__).resolve().parents[3]
    worker_result = run_worker(args, root, checkout)
    if worker_result is not None:
        return worker_result
    root.mkdir(parents=True, exist_ok=False)
    manifest = installed_manifest(args.source_root or checkout)
    write_json(root / "installation.json", manifest)
    if not args.baseline and manifest["all_python_bytes_match_source"] is not True:
        message = "installed_candidate_differs_from_source"
        raise RuntimeError(message)
    _ = shutil.copytree(
        checkout / "tests",
        root / "fixture-suite" / "tests",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    _ = (root / "empty.ini").write_text("[pytest]\n")
    write_json(
        root / "rubric.json",
        {
            "mode": args.mode,
            "model": args.model,
            "baseline": args.baseline,
            "tool_budget": 8,
            "cost_budget": 50,
            "fixture_files": list(FIXTURE_FILES),
            "model_cases": list(MODEL_CASES),
            "oracles": [
                "exact received response",
                "readable rooted PNG and digest",
                "no effect without receipt",
                "different OS PIDs and exactly10 durable receipts",
                "honest incomplete status",
            ],
            "limits": "Synthetic tools/transport; model mode uses Codex. No production writes.",
        },
    )
    health_probe(args, root)
    workers = (
        (
            "pytest",
            "restart-start",
            "rollback-active",
            "restart-drain",
            "rollback-safe",
            "reservation-crash",
            "reservation-restart",
        )
        if args.mode == "fixture"
        else MODEL_CASES
    )
    if args.scenario != "all" and args.scenario not in workers:
        message = f"unknown_canary_scenario:{args.scenario}"
        raise ValueError(message)
    outcomes: list[JsonObject] = []
    for worker in workers:
        if args.scenario not in {"all", worker}:
            continue
        command = [
            sys.executable,
            "-I",
            str(Path(__file__).resolve()),
            "--mode",
            args.mode,
            "--output-root",
            str(root),
            "--model",
            args.model,
            "--codex",
            str(args.codex),
            "--worker",
            worker if args.mode == "fixture" else "model",
            "--case",
            worker,
        ]
        if args.baseline:
            command.append("--baseline")
        if args.baseline_python is not None:
            command.extend(("--baseline-python", str(args.baseline_python)))
        code = run_command(command, root, worker, timeout=2400)
        outcomes.append(
            {
                "case": worker,
                "exit_code": code,
                "expected_exit_code": 73 if worker == "reservation-crash" else 0,
            }
        )
        print(json.dumps(outcomes[-1]), flush=True)  # noqa: T201
    passed = all(item["exit_code"] == item["expected_exit_code"] for item in outcomes)
    write_json(
        root / "summary.json",
        {
            "mode": args.mode,
            "baseline": args.baseline,
            "outcomes": outcomes,
            "passed": passed,
        },
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
