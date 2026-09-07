from __future__ import annotations

import ast
import importlib.util
import json
import sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from urllib.request import Request

MANAGER = Path(__file__).resolve().parents[2] / "docs/operations/agent-server/agent-manager.py"


class Manager(Protocol):
    STATE: Path

    def activate(self, root: Path, candidate: Path) -> None: ...
    def recover(self, root: Path) -> None: ...
    def stage(self, root: Path) -> Path | None: ...
    def github_checks(self, sha: str) -> list[dict[str, object]]: ...
    def bootstrap(self, root: Path) -> None: ...
    def select(self, root: Path, release: Path) -> None: ...


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Manager:
    spec = importlib.util.spec_from_file_location("agent_manager", MANAGER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STATE", tmp_path / "state")
    return cast("Manager", cast("object", module))


def setup(root: Path, manager: Manager) -> tuple[Path, Path]:
    root.mkdir()
    previous, candidate = root / "releases/old", root / "releases/new"
    for release in [previous, candidate]:
        release.mkdir(parents=True)
        _ = (release / "release.json").write_text(json.dumps({"release": release.name}))
    manager.select(root, previous)
    manager.STATE.mkdir()
    with closing(sqlite3.connect(manager.STATE / "runs.sqlite3")) as db, db:
        _ = db.execute("CREATE TABLE records (value TEXT)")
        _ = db.execute("INSERT INTO records VALUES ('approved-and-sent')")
    return previous, candidate


def records(state: Path) -> list[tuple[str]]:
    with closing(sqlite3.connect(state / "runs.sqlite3")) as db, db:
        return cast("list[tuple[str]]", db.execute("SELECT value FROM records").fetchall())


def controls(
    monkeypatch: pytest.MonkeyPatch, manager: Manager, waiter: Callable[..., None]
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def control(*args: str) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(manager, "systemctl", control)
    monkeypatch.setattr(manager, "wait_health", waiter)
    return calls


def test_update_waits_for_quiescence_and_preserves_completed_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager: Manager,
) -> None:
    root = tmp_path / "install"
    _old, candidate = setup(root, manager)
    phases: list[str] = []

    def ready(release: str, **_kwargs: object) -> None:
        assert (root / "maintenance").exists()
        phases.append(release)

    calls = controls(monkeypatch, manager, ready)
    manager.activate(root, candidate)
    assert phases == ["old", "new"]
    assert calls == [("stop", "trace-marketing.service"), ("start", "trace-marketing.service")]
    assert (root / "current").resolve() == candidate
    assert records(manager.STATE) == [("approved-and-sent",)]
    assert not (root / "maintenance").exists()
    assert not (root / "transaction.json").exists()


def test_failed_candidate_restores_database_before_old_process_restarts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager: Manager,
) -> None:
    root = tmp_path / "install"
    previous, candidate = setup(root, manager)

    def ready(release: str, **_kwargs: object) -> None:
        if release == "new":
            with closing(sqlite3.connect(manager.STATE / "runs.sqlite3")) as db, db:
                _ = db.execute("INSERT INTO records VALUES ('candidate-migration')")
            message = "candidate_boot_failed"
            raise RuntimeError(message)
        assert records(manager.STATE) == [("approved-and-sent",)]

    calls = controls(monkeypatch, manager, ready)
    with pytest.raises(RuntimeError, match="candidate_boot_failed"):
        manager.activate(root, candidate)
    assert (root / "current").resolve() == previous
    assert len(calls) == 4
    assert not (root / "maintenance").exists()
    assert (root / "last-failure.json").exists()


def test_busy_agent_is_never_stopped_or_killed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager: Manager,
) -> None:
    root = tmp_path / "install"
    previous, candidate = setup(root, manager)

    def busy(*_args: object, **_kwargs: object) -> None:
        message = "drain_timeout"
        raise RuntimeError(message)

    calls = controls(monkeypatch, manager, busy)
    with pytest.raises(RuntimeError, match="drain_timeout"):
        manager.activate(root, candidate)
    assert calls == []
    assert (root / "current").resolve() == previous
    assert not (root / "maintenance").exists()


def test_interrupted_activation_recovers_without_replaying_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager: Manager,
) -> None:
    root = tmp_path / "install"
    previous, candidate = setup(root, manager)

    def interrupted(release: str, **_kwargs: object) -> None:
        if release == "new":
            raise KeyboardInterrupt

    _ = controls(monkeypatch, manager, interrupted)
    with pytest.raises(KeyboardInterrupt):
        manager.activate(root, candidate)
    assert (root / "maintenance").exists()
    _ = controls(monkeypatch, manager, lambda *_args, **_kwargs: None)
    manager.recover(root)
    assert (root / "current").resolve() == previous
    assert records(manager.STATE) == [("approved-and-sent",)]


def test_committed_recovery_never_rewinds_new_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager: Manager,
) -> None:
    root = tmp_path / "install"
    _old, _candidate = setup(root, manager)
    _ = (root / "transaction.json").write_text(json.dumps({"phase": "committed"}))
    (root / "maintenance").touch()
    calls = controls(monkeypatch, manager, lambda *_args, **_kwargs: None)
    manager.recover(root)
    assert calls == []
    assert records(manager.STATE) == [("approved-and-sent",)]
    assert not (root / "maintenance").exists()


@pytest.mark.parametrize(
    "check",
    [
        (None, "in_progress", "github-actions", "Verify on-prem agent"),
        ("failure", "completed", "github-actions", "Verify on-prem agent"),
        ("cancelled", "completed", "github-actions", "Verify on-prem agent"),
        ("skipped", "completed", "github-actions", "Verify on-prem agent"),
        ("success", "in_progress", "github-actions", "Verify on-prem agent"),
        ("success", "completed", "untrusted-app", "Verify on-prem agent"),
        ("success", "completed", "github-actions", "Some other check"),
    ],
)
def test_main_with_pending_or_failed_ci_is_not_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager: Manager,
    check: tuple[str | None, str, str, str],
) -> None:
    conclusion, status, app, name = check
    root = tmp_path / "install"
    previous, _candidate = setup(root, manager)
    _ = (root / "update.json").write_text(
        json.dumps({"repository": "git@github.com:corca-ai/ads-booster.git"})
    )
    calls: list[list[str]] = []

    def fake(args: list[str], **_kwargs: object) -> str:
        calls.append(args)
        if "rev-parse" in args:
            return "a" * 40
        if args[0] == "gh":
            return json.dumps(
                [
                    {
                        "check_runs": [
                            {
                                "name": name,
                                "app": {"slug": app},
                                "status": status,
                                "conclusion": conclusion,
                            }
                        ]
                    }
                ]
            )
        return ""

    monkeypatch.setattr(manager, "command", fake)

    def checks(_sha: str) -> list[dict[str, object]]:
        return cast("list[dict[str, object]]", json.loads(fake(["gh"])))

    monkeypatch.setattr(manager, "github_checks", checks)
    assert manager.stage(root) is None
    assert (root / "current").resolve() == previous
    assert not any(args[0] in {"uv", "systemctl"} for args in calls)


@pytest.mark.parametrize("other_status", ["completed", "in_progress"])
def test_unrelated_mac_check_does_not_block_agent_protocol_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manager: Manager,
    other_status: str,
) -> None:
    root = tmp_path / "install"
    previous, _candidate = setup(root, manager)
    _ = (root / "update.json").write_text(
        json.dumps({"repository": "git@github.com:corca-ai/ads-booster.git"})
    )
    calls: list[list[str]] = []

    def fake(args: list[str], **_kwargs: object) -> str:
        calls.append(args)
        if "rev-parse" in args:
            return "a" * 40
        if args[0] == "gh":
            return json.dumps(
                [
                    {
                        "check_runs": [
                            {
                                "status": "completed",
                                "conclusion": "success",
                                "name": "Verify on-prem agent",
                                "app": {"slug": "github-actions"},
                            },
                            {
                                "status": other_status,
                                "conclusion": "failure" if other_status == "completed" else None,
                                "name": "Check verified Mac release",
                                "app": {"slug": "github-actions"},
                            },
                        ]
                    }
                ]
            )
        return ""

    monkeypatch.setattr(manager, "command", fake)

    def checks(_sha: str) -> list[dict[str, object]]:
        return cast("list[dict[str, object]]", json.loads(fake(["gh"])))

    monkeypatch.setattr(manager, "github_checks", checks)
    with pytest.raises(RuntimeError, match="main_missing_agent_updater"):
        _ = manager.stage(root)
    assert (root / "current").resolve() == previous
    assert not (root / "last-failure.json").exists()
    with pytest.raises(RuntimeError, match="main_missing_agent_updater"):
        _ = manager.stage(root)
    assert not any(args[0] == "systemctl" for args in calls)


def test_operator_manager_parses_on_ubuntu_system_python() -> None:
    _ = ast.parse(MANAGER.read_text(), feature_version=(3, 10))


def test_bootstrap_waits_for_ci_and_does_not_create_a_current_install(
    tmp_path: Path,
    manager: Manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_checks(args: list[str], **_kwargs: object) -> str:
        if "rev-parse" in args:
            return "a" * 40
        if args[0] == "gh":
            return '[{"check_runs": []}]'
        return ""

    monkeypatch.setattr(manager, "command", no_checks)

    def checks(_sha: str) -> list[dict[str, object]]:
        return [{"check_runs": []}]

    monkeypatch.setattr(manager, "github_checks", checks)
    with pytest.raises(RuntimeError, match="main_not_ready"):
        manager.bootstrap(tmp_path)
    assert not (tmp_path / "current").exists()


def test_bootstrap_selects_verified_candidate_and_preserves_existing_install(
    tmp_path: Path,
    manager: Manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "releases/verified"
    candidate.mkdir(parents=True)

    def staged(_root: Path) -> Path:
        return candidate

    monkeypatch.setattr(manager, "stage", staged)
    manager.bootstrap(tmp_path)
    assert (tmp_path / "current").resolve() == candidate
    with pytest.raises(RuntimeError, match="managed_install_exists"):
        manager.bootstrap(tmp_path)
    assert (tmp_path / "current").resolve() == candidate


def test_public_ci_lookup_paginates_without_credentials(
    manager: Manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Request] = []

    def response(request: Request, *, timeout: int) -> BytesIO:
        assert timeout == 30
        requests.append(request)
        runs: list[dict[str, object]] = (
            [{}] * 100 if len(requests) == 1 else [{"name": "Verify on-prem agent"}]
        )
        return BytesIO(json.dumps({"check_runs": runs}).encode())

    monkeypatch.setattr(manager, "urlopen", response)
    pages = manager.github_checks("a" * 40)
    assert len(pages) == 2
    assert requests[1].full_url.endswith("page=2")
    assert all(request.get_header("Authorization") is None for request in requests)
