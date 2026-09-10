from __future__ import annotations

import ast
import importlib.util
import json
import os
import sqlite3
import sys
import venv
from contextlib import closing
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, Protocol, cast
from urllib.error import HTTPError

import pytest

from ads_booster.cli import server

if TYPE_CHECKING:
    from collections.abc import Callable
    from urllib.request import ProxyHandler, Request

MANAGER = Path(__file__).resolve().parents[2] / "docs/operations/agent-server/agent-manager.py"
LEGACY_MAINTENANCE = "ads_booster.marketing.agent_service.maintenance"
LEGACY_HTTP_API = "ads_booster.marketing.agent_service.http_api"
CURRENT_MAINTENANCE = "ads_booster.agent.service.maintenance"
CURRENT_HTTP_API = "ads_booster.channels.http.http_api"


class Manager(Protocol):
    STATE: Path

    def activate(self, root: Path, candidate: Path) -> None: ...
    def recover(self, root: Path) -> None: ...
    def stage(self, root: Path) -> Path | None: ...
    def github_checks(self, sha: str) -> list[dict[str, object]]: ...
    def bootstrap(self, root: Path) -> None: ...
    def select(self, root: Path, release: Path) -> None: ...
    def probe(self, release: Path) -> None: ...
    def health(self) -> dict[str, object]: ...
    def wait_health(self, release: str, *, drain: bool = False, seconds: int = 60) -> None: ...
    def server_port(self) -> int: ...
    def run(self, root: Path) -> NoReturn: ...
    def knowledge_paths(self) -> tuple[Path, Path, Path] | None: ...


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


def test_update_reads_persistent_knowledge_paths_without_exporting_secrets(
    manager: Manager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager, "CONFIG", tmp_path / "server.json", raising=False)
    names = (
        "TRACE_MARKETING_KNOWLEDGE_ROOT",
        "TRACE_MARKETING_KNOWLEDGE_CONTROL_ROOT",
        "TRACE_MARKETING_KNOWLEDGE_POLICY",
    )
    paths = (tmp_path / "data space", tmp_path / "control", tmp_path / "policy")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    _ = (tmp_path / "agent.env").write_text(
        "SLACK_SECRET=do-not-export\n"
        + "".join(f'{name}="{path}"\n' for name, path in zip(names, paths, strict=True))
    )
    assert manager.knowledge_paths() == paths
    assert "SLACK_SECRET" not in os.environ


def test_degraded_worker_can_drain_but_cannot_activate(
    manager: Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    degraded = {
        "status": "degraded",
        "owner": "on_prem_agent",
        "update_protocol": 1,
        "release": "candidate",
        "maintenance": True,
        "active": 0,
        "knowledge_worker": "stopped",
    }
    monkeypatch.setattr(manager, "health", lambda: degraded)
    manager.wait_health("candidate", drain=True, seconds=1)
    with pytest.raises(RuntimeError, match="candidate_health_failed"):
        manager.wait_health("candidate", seconds=1)


def test_health_reads_degraded_response_body(
    manager: Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b'{"status":"degraded","owner":"on_prem_agent"}'
    error = HTTPError("http://127.0.0.1:8090/health", 503, "degraded", Message(), BytesIO(payload))

    class Opener:
        def open(self, _url: str, *, timeout: int) -> BytesIO:
            assert timeout == 3
            raise error

    def opener(_handler: ProxyHandler) -> Opener:
        return Opener()

    monkeypatch.setattr(manager, "build_opener", opener)
    assert manager.health() == {"status": "degraded", "owner": "on_prem_agent"}


def test_candidate_start_backfills_before_exec_with_old_environment(
    manager: Manager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "managed"
    _, candidate = setup(root, manager)
    manager.select(root, candidate)
    (candidate / ".venv").symlink_to(Path(sys.prefix), target_is_directory=True)
    config = tmp_path / "config"
    config.mkdir()
    _ = (config / "agent.env").write_text('TRACE_MARKETING_TENANT="fixture"\n')
    monkeypatch.setattr(manager, "CONFIG", config / "server.json", raising=False)
    monkeypatch.setenv("TRACE_MARKETING_TENANT", "fixture")
    monkeypatch.setenv("TRACE_MARKETING_MODEL", "fixture")
    for name in (
        "TRACE_MARKETING_KNOWLEDGE_ROOT",
        "TRACE_MARKETING_KNOWLEDGE_CONTROL_ROOT",
        "TRACE_MARKETING_KNOWLEDGE_POLICY",
    ):
        monkeypatch.delenv(name, raising=False)

    def execve(_path: str, _argv: list[str], environment: dict[str, str]) -> NoReturn:
        assert environment["TRACE_MARKETING_KNOWLEDGE_ROOT"] == str(root / "knowledge")
        assert (config / "knowledge-policy.json").is_file()
        assert "TRACE_MARKETING_KNOWLEDGE_ROOT=" in (config / "agent.env").read_text()
        raise SystemExit

    monkeypatch.setattr(os, "execve", execve)
    with pytest.raises(SystemExit):
        manager.run(root)


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


def module_path(site_packages: Path, module: str) -> Path:
    parts = module.split(".")
    package = site_packages.joinpath(*parts[:-1])
    package.mkdir(parents=True, exist_ok=True)
    for parent in [package, *package.parents]:
        if parent.name == "site-packages":
            break
        _ = (parent / "__init__.py").touch()
    return package / f"{parts[-1]}.py"


def candidate_venv(
    release: Path,
    *,
    maintenance_module: str,
    api_module: str,
    protocol: int = 1,
    api_fields: tuple[str, ...] = ("slack_only", "slack_events"),
) -> Path:
    executable = release / ".venv/bin/python"
    if not executable.exists():
        venv.EnvBuilder(with_pip=False).create(release / ".venv")
    runtime = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_packages = release / ".venv/lib" / runtime
    package_root = site_packages / "site-packages"
    _ = module_path(package_root, maintenance_module).write_text(f"UPDATE_PROTOCOL = {protocol}\n")
    api_parameters = tuple(f"    {field}: object" for field in api_fields)
    _ = module_path(package_root, api_module).write_text(
        "\n".join(
            (
                "from dataclasses import dataclass",
                "",
                "@dataclass",
                "class MarketingAgentApi:",
                *api_parameters,
                "",
            )
        )
    )
    calls = release / "probe-cli-calls.jsonl"
    cli = release / ".venv/bin/trace-marketing"
    _ = cli.write_text(
        "\n".join(
            (
                f"#!{executable}",
                "import json",
                "import sys",
                f"calls = {str(calls)!r}",
                "with open(calls, 'a', encoding='utf-8') as stream:",
                "    stream.write(json.dumps(sys.argv[1:]) + '\\n')",
                "manifest = [",
                "    'server', 'manifest', '--origin',",
                "    'https://agent.example.com', '--bootstrap',",
                "]",
                "if sys.argv[1:] == ['service', 'doctor']:",
                "    print(json.dumps({'ready': True}))",
                "elif sys.argv[1:] == manifest:",
                "    pass",
                "else:",
                "    raise SystemExit(2)",
                "",
            )
        )
    )
    _ = cli.chmod(0o755)
    return calls


@pytest.mark.parametrize(
    ("maintenance_module", "api_module"),
    [(LEGACY_MAINTENANCE, LEGACY_HTTP_API), (CURRENT_MAINTENANCE, CURRENT_HTTP_API)],
)
def test_probe_accepts_each_supported_canonical_namespace_when_candidate_is_ready(
    tmp_path: Path,
    manager: Manager,
    maintenance_module: str,
    api_module: str,
) -> None:
    # Given: an isolated candidate venv with only one supported namespace layout.
    release = tmp_path / "release"
    release.mkdir()
    calls = candidate_venv(
        release,
        maintenance_module=maintenance_module,
        api_module=api_module,
    )

    # When: the standalone manager probes that candidate with its installed interpreter.
    manager.probe(release)

    # Then: it accepts the candidate and preserves its service doctor and manifest checks.
    assert calls.read_text().splitlines() == [
        '["service", "doctor"]',
        '["server", "manifest", "--origin", "https://agent.example.com", "--bootstrap"]',
    ]


@pytest.mark.parametrize(
    ("protocol", "api_fields"),
    [(0, ("slack_only", "slack_events")), (1, ("slack_only",))],
    ids=["protocol", "api_shape"],
)
def test_probe_rejects_invalid_candidate_contract_before_doctor_or_activation(
    tmp_path: Path,
    manager: Manager,
    protocol: int,
    api_fields: tuple[str, ...],
) -> None:
    # Given: a new-layout candidate whose protocol or API shape violates the updater contract.
    root = tmp_path / "install"
    previous, release = setup(root, manager)
    calls = candidate_venv(
        release,
        maintenance_module=CURRENT_MAINTENANCE,
        api_module=CURRENT_HTTP_API,
        protocol=protocol,
        api_fields=api_fields,
    )

    # When: the manager evaluates the candidate before starting an activation transaction.
    with pytest.raises(RuntimeError, match="command_failed:python"):
        manager.probe(release)

    # Then: it leaves the current release selected and never reaches the candidate CLI surface.
    assert (root / "current").resolve() == previous
    assert not calls.exists()


def test_probe_does_not_fallback_when_a_supported_namespace_has_an_unrelated_import_failure(
    tmp_path: Path,
    manager: Manager,
) -> None:
    # Given: a legacy package with a broken dependency and an otherwise valid new package.
    release = tmp_path / "release"
    release.mkdir()
    calls = candidate_venv(
        release,
        maintenance_module=LEGACY_MAINTENANCE,
        api_module=LEGACY_HTTP_API,
    )
    legacy_package = (
        release
        / ".venv/lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages/ads_booster/marketing"
    )
    _ = (legacy_package / "__init__.py").write_text(
        "raise ModuleNotFoundError(name='unexpected_dependency')\n"
    )
    _ = candidate_venv(
        release,
        maintenance_module=CURRENT_MAINTENANCE,
        api_module=CURRENT_HTTP_API,
    )

    # When: the manager searches for a canonical service namespace.
    with pytest.raises(RuntimeError, match="command_failed:python"):
        manager.probe(release)

    # Then: the unrelated legacy failure rejects the candidate instead of falling through.
    assert not calls.exists()


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
    assert sorted(path.name for path in (root / "releases").iterdir()) == ["new", "old"]
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


def test_candidate_cannot_remove_the_installed_operator_surface(
    manager: Manager,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def old_candidate(args: list[str], **_kwargs: object) -> str:
        if "server" in args:
            message = "command_failed:trace-marketing"
            raise RuntimeError(message)
        return '{"ready": true}'

    monkeypatch.setattr(manager, "command", old_candidate)
    with pytest.raises(RuntimeError, match="command_failed:trace-marketing"):
        manager.probe(tmp_path)


@pytest.mark.parametrize("configured", [None, 8090, 18090])
def test_launch_update_health_and_status_share_persistent_port(
    manager: Manager,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    configured: int | None,
) -> None:
    config = tmp_path / "config/server.json"
    config.parent.mkdir()
    settings: dict[str, object] = {"origin": "https://agent.example.com", "tunnel": False}
    if configured is not None:
        settings["port"] = configured
    _ = config.write_text(json.dumps(settings))
    monkeypatch.setattr(manager, "CONFIG", config, raising=False)
    monkeypatch.setattr(server, "CONFIG", config.parent)
    monkeypatch.setattr(server, "ROOT", tmp_path / "install")
    root = tmp_path / "install"
    (root / "current").mkdir(parents=True)
    _ = (root / "current/release.json").write_text('{"release":"fixture"}')
    port = configured or 8090
    requested: list[str] = []

    class Opener:
        def open(self, url: str, *, timeout: int) -> BytesIO:
            assert timeout == 3
            requested.append(url)
            return BytesIO(b'{"owner":"on_prem_agent"}')

    def opener(*_args: object) -> Opener:
        return Opener()

    monkeypatch.setattr(manager, "build_opener", opener)
    assert manager.health()["owner"] == "on_prem_agent"
    assert requested == [f"http://127.0.0.1:{port}/health"]

    def request(url: str) -> dict[str, object]:
        requested.append(url)
        return {"owner": "on_prem_agent"}

    def execute(*_args: str) -> str:
        return "yes"

    monkeypatch.setattr(server, "json_request", request)
    monkeypatch.setattr(server, "execute", execute)
    server.status()
    assert requested[1] == requested[0]
    monkeypatch.setenv("TRACE_MARKETING_MODEL", "fixture-model")
    monkeypatch.setenv("TRACE_MARKETING_TENANT", "fixture")

    def execve(_path: str, argv: list[str], _env: dict[str, str]) -> NoReturn:
        assert argv[argv.index("--port") + 1] == str(port)
        raise SystemExit

    monkeypatch.setattr(os, "execve", execve)
    with pytest.raises(SystemExit):
        manager.run(root)


@pytest.mark.parametrize("port", [0, -1, 65536, True, "8090"])
def test_invalid_port_fails_before_contacting_any_service(
    manager: Manager,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    port: object,
) -> None:
    config = tmp_path / "server.json"
    _ = config.write_text(json.dumps({"port": port}))
    monkeypatch.setattr(manager, "CONFIG", config)
    with pytest.raises(RuntimeError, match="server_port_requires_integer"):
        _ = manager.server_port()
    with pytest.raises(RuntimeError, match="server_port_requires_integer"):
        _ = server.server_port({"port": port})
