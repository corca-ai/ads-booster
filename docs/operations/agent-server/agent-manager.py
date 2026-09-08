"""Linux main-agent installer/updater. Python 3.10+ standard library only.

The timer runs the manager in the selected release, so updater changes follow main too.
No agent credentials are needed by the update unit. Git credentials stay in SSH/credential helper.
"""

# pyright: reportAny=false, reportExplicitAny=false
# JSON and argparse are the standalone Python 3.10 operator boundary.
# ruff: noqa: EM101, EM102, T201, TRY300, TRY301, INP001
# Standalone CLI prints sanitized status; rollback spans the whole transaction.
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, NoReturn, cast
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

PROTOCOL = 1
MAX_PORT = 65535
ROOT = Path.home() / ".local/share/trace-marketing-server"
CONFIG = Path.home() / ".config/trace-marketing/server.json"
STATE = Path.home() / ".local/state/trace-marketing"
UNIT = "trace-marketing.service"
REPO = "git@github.com:corca-ai/ads-booster.git"


def command(args: list[str], *, cwd: Path | None = None, timeout: int = 600) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND="ssh -oBatchMode=yes")
    # Never include raw subprocess output in the journal (could contain credentials).
    result = subprocess.run(  # noqa: S603 - fixed argv owners; credential-free repo allowlist.
        args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError(f"command_failed:{Path(args[0]).name}")
    return result.stdout.strip()


def atomic_json(path: Path, value: dict[str, str]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    _ = temporary.replace(path)
    sync_directory(path.parent)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def select(root: Path, release: Path) -> None:
    temporary = root / "current.next"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(release, target_is_directory=True)
    _ = temporary.replace(root / "current")
    sync_directory(root)


def read_json(path: Path) -> dict[str, Any]:
    value: Any = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise TypeError("json_object_required")
    return cast("dict[str, Any]", value)


def server_port() -> int:
    settings = read_json(CONFIG) if CONFIG.exists() else {}
    port = settings.get("port", 8090)
    if type(port) is not int or not 1 <= port <= MAX_PORT:
        raise RuntimeError("server_port_requires_integer_1_to_65535")
    return port


def health() -> dict[str, Any]:
    try:
        # Deliberately fixed loopback URL; never honor proxy environment variables.
        with build_opener(ProxyHandler({})).open(
            f"http://127.0.0.1:{server_port()}/health", timeout=3
        ) as response:
            value = json.load(response)
            return cast("dict[str, Any]", value) if isinstance(value, dict) else {}
    except (OSError, ValueError, URLError):
        return {}


def wait_health(release: str, *, drain: bool = False, seconds: int = 60) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = health()
        if (
            value.get("owner") == "on_prem_agent"
            and value.get("update_protocol") == PROTOCOL
            and value.get("release") == release
            and value.get("maintenance") is True
            and value.get("active") == 0
        ):
            return
        time.sleep(1)
    raise RuntimeError("drain_timeout" if drain else "candidate_health_failed")


def systemctl(*args: str) -> str:
    return command(["systemctl", "--user", *args], timeout=90)


def probe(release: Path) -> None:
    executable = release / ".venv/bin/python"
    # Fails before touching the running service if main predates the updater/slack-only contract.
    code = (
        "from ads_booster.marketing.agent_service.maintenance import UPDATE_PROTOCOL; "
        "from ads_booster.marketing.agent_service.http_api import MarketingAgentApi; "
        "assert UPDATE_PROTOCOL == 1; "
        "assert 'slack_only' in MarketingAgentApi.__dataclass_fields__; "
        "assert 'slack_events' in MarketingAgentApi.__dataclass_fields__"
    )
    _ = command([str(executable), "-c", code])
    value = json.loads(command([str(release / ".venv/bin/trace-marketing"), "service", "doctor"]))
    if value.get("ready") is not True:
        raise RuntimeError("candidate_doctor_not_ready")
    # A verified but older main must not remove the installed operator command/assets.
    _ = command(
        [
            str(release / ".venv/bin/trace-marketing"),
            "server",
            "manifest",
            "--origin",
            "https://agent.example.com",
            "--bootstrap",
        ]
    )


def stage(root: Path) -> Path | None:
    config = read_json(root / "update.json")
    # Operator-selected, credential-free repository URL; no URLs containing embedded tokens.
    repo = config["repository"]
    if repo not in {REPO, "https://github.com/corca-ai/ads-booster.git"}:
        raise RuntimeError("repository_not_allowed")
    mirror = root / "repository.git"
    if not mirror.exists():
        _ = command(["git", "init", "--bare", str(mirror)])
    _ = command(
        [
            "git",
            "--git-dir",
            str(mirror),
            "fetch",
            "--no-tags",
            repo,
            "+refs/heads/main:refs/heads/main",
        ]
    )
    sha = command(["git", "--git-dir", str(mirror), "rev-parse", "refs/heads/main"])
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("invalid_main_sha")
    current = (
        read_json(root / "current/release.json")["release"]
        if (root / "current/release.json").exists()
        else "uninstalled"
    )
    if sha == current:
        atomic_json(root / "last-check.json", {"release": sha, "result": "up_to_date"})
        return None
    if re.fullmatch(r"[0-9a-f]{40}", current):
        _ = command(["git", "--git-dir", str(mirror), "merge-base", "--is-ancestor", current, sha])
    checks = github_checks(sha)
    runs = [run for page in checks for run in page.get("check_runs", [])]
    required = [
        run
        for run in runs
        if run.get("name") == "Verify on-prem agent"
        and run.get("app", {}).get("slug") == "github-actions"
    ]
    # This check owns server and tool-adapter compatibility. Mac publishing is independent.
    if not required or any(
        run.get("status") != "completed" or run.get("conclusion") != "success" for run in required
    ):
        atomic_json(root / "last-check.json", {"release": sha, "result": "waiting_for_ci"})
        return None
    failed = root / "last-failure.json"
    if failed.exists() and read_json(failed).get("release") == sha:
        atomic_json(root / "last-check.json", {"release": sha, "result": "quarantined"})
        return None  # Quarantine a failing SHA until a new main commit or explicit operator retry.
    release = root / "releases" / (sha + "-" + uuid.uuid4().hex[:8])
    release.mkdir(parents=True)
    try:
        source = release / "source"
        _ = command(["git", "clone", "--no-hardlinks", str(mirror), str(source)])
        _ = command(["git", "checkout", "--detach", sha], cwd=source)
        manager = source / "docs/operations/agent-server/agent-manager.py"
        if not manager.is_file():
            raise RuntimeError("main_missing_agent_updater")
        # uv.lock owns exact application dependencies. A locked install rejects stale lockfiles.
        _ = command(
            ["uv", "sync", "--locked", "--no-dev", "--no-editable", "--python", "3.14"],
            cwd=source,
            timeout=1200,
        )
        (release / ".venv").symlink_to(source / ".venv", target_is_directory=True)
        _ = shutil.copy2(manager, release / "agent-manager.py")
        atomic_json(release / "release.json", {"release": sha})
        probe(release)
        atomic_json(root / "last-check.json", {"release": sha, "result": "candidate_ready"})
        return release
    except Exception:
        # Staging cannot affect the running agent. Network/dependency failures may be
        # retried by the next check; only failed activation quarantines a release.
        atomic_json(root / "last-check.json", {"release": sha, "result": "staging_failed"})
        shutil.rmtree(release)
        raise


def github_checks(sha: str) -> list[dict[str, Any]]:
    """Read public checks without gh or a GitHub login; fail closed on API errors."""
    page_size = 100
    pages: list[dict[str, Any]] = []
    endpoint = f"https://api.github.com/repos/corca-ai/ads-booster/commits/{sha}"
    for page in range(1, 101):
        request = Request(  # noqa: S310 - fixed GitHub origin.
            endpoint + f"/check-runs?per_page=100&filter=latest&page={page}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "trace-marketing"},
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed GitHub origin.
            value = json.load(response)
        if not isinstance(value, dict):
            raise TypeError("github_checks_invalid_response")
        value = cast("dict[str, Any]", value)
        if not isinstance(value.get("check_runs"), list):
            raise TypeError("github_checks_invalid_response")
        pages.append(value)
        if len(value["check_runs"]) < page_size:
            return pages
    raise RuntimeError("github_checks_pagination_limit")


def restore_state(backup: Path) -> None:
    # Both processes must be stopped. Copy first; retain failed state for explicit reconciliation.
    temporary = STATE.with_name("trace-marketing-restore-" + uuid.uuid4().hex)
    _ = shutil.copytree(backup, temporary)
    if STATE.exists():
        _ = STATE.rename(STATE.with_name("trace-marketing-quarantine-" + uuid.uuid4().hex))
    _ = temporary.rename(STATE)
    sync_directory(STATE.parent)


def recover(root: Path) -> None:
    journal_path = root / "transaction.json"
    if not journal_path.exists():
        return
    journal = read_json(journal_path)
    gate = root / "maintenance"
    if journal["phase"] == "committed":
        # Work may have resumed. Never rewind records after activation.
        gate.unlink(missing_ok=True)
        journal_path.unlink()
        return
    gate.touch(mode=0o600)
    previous = Path(journal["previous"])
    if journal["phase"] == "switching":
        _ = systemctl("stop", UNIT)
        restore_state(Path(journal["backup"]))
        select(root, previous)
    # During draining/prepared the pointer and canonical state were never changed.
    _ = systemctl("start", UNIT)
    wait_health(read_json(previous / "release.json")["release"])
    atomic_json(
        root / "last-failure.json",
        {
            "release": journal["release"],
            "error": "update_rolled_back",
        },
    )
    gate.unlink(missing_ok=True)
    journal_path.unlink()


def activate(root: Path, candidate: Path) -> None:
    previous = (root / "current").resolve(strict=True)
    old_sha = read_json(previous / "release.json")["release"]
    sha = read_json(candidate / "release.json")["release"]
    gate = root / "maintenance"
    journal_path = root / "transaction.json"
    journal = {"phase": "draining", "previous": str(previous), "release": sha}
    atomic_json(journal_path, journal)
    gate.touch(mode=0o600)
    sync_directory(root)
    try:
        wait_health(old_sha, drain=True, seconds=300)
    except Exception:
        # Existing work is not stopped or killed when it cannot reach a safe boundary.
        gate.unlink(missing_ok=True)
        journal_path.unlink()
        raise
    try:
        _ = systemctl("stop", UNIT)
        backup = root / "backups" / uuid.uuid4().hex
        STATE.mkdir(parents=True, exist_ok=True)
        _ = shutil.copytree(STATE, backup)
        journal.update(phase="switching", backup=str(backup))
        atomic_json(journal_path, journal)
        select(root, candidate)
        _ = systemctl("start", UNIT)
        # Passive candidate: HTTP writes, queue recovery, schedules and outbound sends are gated.
        wait_health(sha)
        journal["phase"] = "committed"
        atomic_json(journal_path, journal)
        gate.unlink()
        journal_path.unlink()
        atomic_json(root / "last-success.json", {"release": sha, "previous": old_sha})
    except Exception:
        recover(root)
        raise


def install(root: Path, wheel: Path, requirements: Path) -> None:
    if (root / "current").exists() or (root / "current").is_symlink():
        raise RuntimeError("managed_install_exists")
    release = root / "releases" / ("bootstrap-" + uuid.uuid4().hex)
    release.mkdir(parents=True)
    _ = command(["uv", "venv", "--python", "3.14", str(release / ".venv")])
    _ = command(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(release / ".venv/bin/python"),
            "-r",
            str(requirements),
        ],
        timeout=1200,
    )
    _ = command(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(release / ".venv/bin/python"),
            "--no-deps",
            str(wheel),
        ]
    )
    _ = shutil.copy2(__file__, release / "agent-manager.py")
    atomic_json(release / "release.json", {"release": release.name})
    probe(release)
    atomic_json(root / "update.json", {"repository": REPO})
    select(root, release)


def bootstrap(root: Path) -> None:
    """Install verified main directly, without a locally delivered wheel or ZIP."""
    if (root / "current").exists() or (root / "current").is_symlink():
        raise RuntimeError("managed_install_exists:use_trace-marketing_server_update")
    atomic_json(root / "update.json", {"repository": "https://github.com/corca-ai/ads-booster.git"})
    release = stage(root)
    if release is None:
        raise RuntimeError("main_not_ready:wait_for_Verify_on-prem_agent")
    select(root, release)


def install_source(root: Path, checkout: Path) -> None:
    """Install a committed developer checkout, explicitly outside the verified channel."""
    if (root / "current").exists() or (root / "current").is_symlink():
        raise RuntimeError("managed_install_exists")
    sha = command(["git", "rev-parse", "HEAD"], cwd=checkout)
    release = root / "releases" / ("source-" + sha + "-" + uuid.uuid4().hex[:8])
    source = release / "source"
    release.mkdir(parents=True)
    _ = command(["git", "clone", "--no-local", str(checkout), str(source)])
    _ = command(["git", "checkout", "--detach", sha], cwd=source)
    _ = command(
        ["uv", "sync", "--locked", "--no-dev", "--no-editable", "--python", "3.14"],
        cwd=source,
        timeout=1200,
    )
    (release / ".venv").symlink_to(source / ".venv", target_is_directory=True)
    _ = shutil.copy2(
        source / "docs/operations/agent-server/agent-manager.py", release / "agent-manager.py"
    )
    atomic_json(release / "release.json", {"release": release.name})
    probe(release)
    atomic_json(root / "update.json", {"repository": "https://github.com/corca-ai/ads-booster.git"})
    select(root, release)


def run(root: Path) -> NoReturn:
    release = (root / "current").resolve(strict=True)
    env = dict(
        os.environ,
        TRACE_MARKETING_MAINTENANCE_FILE=str(root / "maintenance"),
        TRACE_MARKETING_RELEASE=read_json(release / "release.json")["release"],
    )
    executable = str(release / ".venv/bin/trace-marketing")
    os.execve(  # noqa: S606 - exact installed executable, no shell.
        executable,
        [
            executable,
            "service",
            "run",
            "--model",
            env["TRACE_MARKETING_MODEL"],
            "--tenant",
            env["TRACE_MARKETING_TENANT"],
            "--home",
            str(STATE),
            "--host",
            "127.0.0.1",
            "--port",
            str(server_port()),
        ],
        env,
    )


def main() -> None:  # noqa: C901 - standalone action dispatch under one process lock.
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "action", choices=["install", "source", "bootstrap", "run", "update", "status"]
    )
    _ = parser.add_argument("--source", type=Path)
    _ = parser.add_argument("--root", type=Path, default=ROOT)
    _ = parser.add_argument("--wheel", type=Path)
    _ = parser.add_argument("--requirements", type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    _ = os.umask(0o077)
    root.mkdir(parents=True, exist_ok=True)
    if args.action == "run":
        run(root)
    if args.action == "status":
        print(
            json.dumps(
                {
                    name: read_json(root / name) if (root / name).exists() else None
                    for name in [
                        "current/release.json",
                        "last-success.json",
                        "last-failure.json",
                        "transaction.json",
                    ]
                }
            )
        )
        return
    with (root / "update.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("update_already_running")
            return
        if args.action == "bootstrap":
            bootstrap(root)
        elif args.action == "source":
            if args.source is None:
                parser.error("source requires --source DIRECTORY")
            install_source(root, args.source.resolve())
        elif args.action == "install":
            if args.wheel is None or args.requirements is None:
                parser.error("install requires --wheel and --requirements")
            install(root, args.wheel.resolve(), args.requirements.resolve())
        else:
            if sys.platform != "linux":
                raise RuntimeError("updates_require_linux_systemd")
            recover(root)
            candidate = stage(root)
            if candidate is not None:
                activate(root, candidate)
    print("agent_" + args.action + "_complete")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - sanitize operator boundary output.
        # Error types only: no subprocess output, settings, credentials or request content.
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print("agent_manager_failed:" + detail, file=sys.stderr)
        sys.exit(1)
