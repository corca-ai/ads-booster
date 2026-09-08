"""Installed Linux operator surface for Slack and an existing Cloudflare Tunnel."""
# ruff: noqa: EM101, EM102 - stable, sanitized operator error codes.

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import typer

from ads_booster.knowledge.configuration import (
    KnowledgeSettings,
    initialize_knowledge_store,
)
from ads_booster.knowledge.erase_ledger import EraseLedger
from ads_booster.marketing.agent_service.github_issues import (
    GitHubIssues,
    GitHubRejectedError,
    validate_token,
)

if TYPE_CHECKING:
    from http.client import HTTPResponse

app = typer.Typer(no_args_is_help=True, help="Install settings and operate the Linux Slack agent.")
MAX_PORT = 65535
ROOT = Path.home() / ".local/share/trace-marketing-server"
CONFIG = Path.home() / ".config/trace-marketing"
UNITS = Path.home() / ".config/systemd/user"
OWNED = "# Managed by trace-marketing server setup\n"
SERVICE = "trace-marketing.service"
TUNNEL = "trace-marketing-tunnel.service"
TIMER = "trace-marketing-update.timer"


def execute(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=60)  # noqa: S603
    if result.returncode:
        raise RuntimeError(f"command_failed:{Path(args[0]).name}")
    return result.stdout.strip()


def private_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    if path.is_symlink():
        raise RuntimeError("refusing_symlink")
    # Unique exclusive files allow recovery after a process dies during a write.
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w") as stream:
            _ = stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        _ = temp.replace(path)
        path.chmod(0o600)
    finally:
        temp.unlink(missing_ok=True)


def json_request(url: str, token: str | None = None) -> dict[str, object]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = Request(url, headers=headers, method="POST" if token else "GET")  # noqa: S310
    with cast("HTTPResponse", urlopen(request, timeout=10)) as response:  # noqa: S310
        value = cast("object", json.load(response))
    if not isinstance(value, dict):
        raise TypeError("invalid_json_response")
    return cast("dict[str, object]", value)


def origin_value(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or any(c.isspace() for c in value)
    ):
        raise RuntimeError("public_origin_requires_https_hostname")
    labels = parsed.hostname.split(".")
    if not all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels
    ):
        raise RuntimeError("public_origin_requires_dns_hostname")
    return value.rstrip("/")


def env_value(value: str) -> str:
    if any(c in value for c in "\n\r\x00"):
        raise RuntimeError("invalid_multiline_setting")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def executable(name: str) -> str:
    value = shutil.which(name)
    if value is None:
        raise RuntimeError(f"missing_prerequisite:{name}")
    return value


def unit_arg(value: str) -> str:
    return env_value(value).replace("%", "%%")


def resources() -> Path:
    path = Path(__file__).parents[1] / "server_assets"
    if not path.is_dir():
        raise RuntimeError("installed_server_assets_missing")
    return path


def render_units(tunnel: bool) -> dict[str, str]:
    source = resources()
    paths = list(dict.fromkeys(str(Path(executable(n)).parent) for n in ["uv", "git", "codex"]))
    path_line = "Environment=" + env_value(
        "PATH=" + ":".join([*paths, "/usr/local/bin", "/usr/bin", "/bin"])
    )
    units: dict[str, str] = {}
    for name in [SERVICE, "trace-marketing-update.service", TIMER]:
        lines = (source / name).read_text().splitlines()
        units[name] = (
            OWNED
            + "\n".join(
                path_line.replace("%", "%%") if line.startswith("Environment=PATH=") else line
                for line in lines
            )
            + "\n"
        )
    if tunnel:
        cloudflared = executable("cloudflared")
        help_text = execute(cloudflared, "tunnel", "run", "--help")
        if "--token-file" not in help_text:
            raise RuntimeError("cloudflared_token_file_support_required")
        units[TUNNEL] = OWNED + "\n".join(
            [
                "[Unit]",
                "Description=Trace Marketing Cloudflare Tunnel",
                "After=network-online.target",
                "[Service]",
                "Type=simple",
                "ExecStart="
                + unit_arg(cloudflared)
                + " tunnel --no-autoupdate run --token-file "
                + unit_arg(str(CONFIG / "tunnel.token")),
                "Restart=on-failure",
                "RestartSec=5",
                "UMask=0077",
                "NoNewPrivileges=true",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        )
    for name in units:
        target = UNITS / name
        if target.is_symlink() or (target.exists() and not target.read_text().startswith(OWNED)):
            raise RuntimeError(f"existing_unit_preserved:{name}")
    return units


def prompt(message: str, default: str = "") -> str:
    return cast("str", typer.prompt(message, default=default or None, type=str))


def interactive_terminal() -> bool:
    return sys.stdin.isatty()


def setup_preflight() -> None:
    if not interactive_terminal():
        raise RuntimeError("setup_requires_interactive_terminal:run_in_Termius")
    if sys.platform != "linux":
        raise RuntimeError("setup_requires_linux")
    if not (ROOT / "current/agent-manager.py").is_file():
        raise RuntimeError("managed_main_install_required")
    if (CONFIG / "agent.env").exists() or (CONFIG / "slack-installation.json").exists():
        raise RuntimeError("existing_config_preserved:use_server_status")


def setup_config() -> None:
    if (CONFIG / "setup-pending.json").is_file():
        finish_setup()
        return
    if (CONFIG / "server.json").is_file():
        typer.echo("이미 설정되어 있습니다. 다음: trace-marketing server doctor / server start")
        return
    setup_preflight()
    origin = origin_value(prompt("공개 HTTPS 주소 (예: https://agent.example.com)"))
    model = prompt("이 서버 Codex에서 사용할 모델명")
    tenant = prompt("워크스페이스 이름", default="marketing")
    app_id = prompt("Slack App ID")
    channel = prompt("기본/허용 Slack Channel ID")
    members = [v.strip() for v in prompt("허용 Slack 사용자 ID (쉼표 구분)").split(",")]
    approvers = [v.strip() for v in prompt("승인 담당 사용자 ID (쉼표 구분)").split(",")]
    if not re.fullmatch(r"A[A-Z0-9]+", app_id) or not re.fullmatch(r"[CG][A-Z0-9]+", channel):
        raise RuntimeError("invalid_slack_app_or_channel_id")
    if not all(re.fullmatch(r"[UW][A-Z0-9]+", v) for v in members) or not set(approvers) <= set(
        members
    ):
        raise RuntimeError("invalid_slack_members_or_approvers")
    token = getpass.getpass("Bot User OAuth Token (화면에 표시되지 않음): ").strip()
    signing = getpass.getpass("Signing Secret (화면에 표시되지 않음): ").strip()
    if not token.startswith("xoxb-") or not signing:
        raise RuntimeError("slack_secrets_required")
    identity = json_request("https://slack.com/api/auth.test", token)
    if identity.get("ok") is not True or not identity.get("bot_id"):
        raise RuntimeError("slack_bot_auth_failed")
    team, bot = str(identity.get("team_id", "")), str(identity.get("user_id", ""))
    if not re.fullmatch(r"T[A-Z0-9]+", team) or not re.fullmatch(r"[UW][A-Z0-9]+", bot):
        raise RuntimeError("invalid_slack_identity")
    tunnel = typer.confirm(
        "기존 Cloudflare 터널의 전용 커넥터를 이 계정에서 실행할까요?", default=True
    )
    tunnel_token = (
        getpass.getpass("마케팅 터널 토큰 (화면에 표시되지 않음): ").strip() if tunnel else ""
    )
    if tunnel and not tunnel_token:
        raise RuntimeError("tunnel_token_required")
    units = render_units(tunnel)
    settings = {
        "TRACE_MARKETING_SLACK_ONLY": "1",
        "TRACE_MARKETING_PUBLIC_ORIGIN": origin,
        "TRACE_MARKETING_MODEL": model,
        "TRACE_MARKETING_TENANT": tenant,
        "TRACE_MARKETING_SLACK_BOT_TOKEN": token,
        "TRACE_MARKETING_SLACK_SIGNING_SECRET": signing,
        "TRACE_MARKETING_SLACK_BOT_USER_ID": bot,
        "TRACE_MARKETING_SLACK_CHANNEL_ID": channel,
        "TRACE_MARKETING_SLACK_ALLOWED_CHANNEL_IDS": channel,
        "TRACE_MARKETING_SLACK_ALLOW_DM": "1",
        "TRACE_MARKETING_SLACK_INSTALLATION": str(CONFIG / "slack-installation.json"),
        "TRACE_MARKETING_KNOWLEDGE_ROOT": str(ROOT / "knowledge"),
        "TRACE_MARKETING_KNOWLEDGE_CONTROL_ROOT": str(CONFIG / "knowledge-control"),
        "TRACE_MARKETING_KNOWLEDGE_POLICY": str(CONFIG / "knowledge-policy.json"),
    }
    environment = "\n".join(f"{k}={env_value(v)}" for k, v in settings.items()) + "\n"
    installation = {
        "app_id": app_id,
        "team_id": team,
        "tenant_id": tenant,
        "members": [
            {"slack_user_id": v, "member_id": v, "can_approve": v in approvers}
            for v in dict.fromkeys(members)
        ],
    }
    files = {
        "slack-installation.json": json.dumps(installation, indent=2),
        "agent.env": environment,
        **({"tunnel.token": tunnel_token} if tunnel else {}),
        **units,
        "server.json": json.dumps({"origin": origin, "tunnel": tunnel, "port": 8090}),
        "knowledge-policy.json": json.dumps(
            {
                "schema": "trace.knowledge-local-policy.v1",
                "workspace_id": tenant,
                "policy_epoch": 1,
                "capabilities": ["read", "write", "schedule", "share", "purge"],
                "brand_voice_brand_ids": [],
            }
        ),
        "knowledge-control/identity.json": json.dumps(
            {
                "schema": "trace.knowledge-local-identity.v1",
                "actor_id": f"local-owner-{os.getuid()}",
                "workspace_id": tenant,
                "member_id": f"local-member-{os.getuid()}",
                "session_id": "local-admin-session",
            }
        ),
    }
    for name in ["slack-app-bootstrap-manifest.json", "slack-app-manifest.json"]:
        files[name] = (
            (resources() / name).read_text().replace("https://marketing-agent.borca.ai", origin)
        )
    previous = {
        name: setup_target(name).read_text() if setup_target(name).exists() else None
        for name in files
    }
    private_write(CONFIG / "setup-pending.json", json.dumps({"files": files, "previous": previous}))
    finish_setup()


def setup_target(name: str) -> Path:
    if name in {SERVICE, TIMER, TUNNEL, "trace-marketing-update.service"}:
        return UNITS / name
    if name in {
        "agent.env",
        "slack-installation.json",
        "tunnel.token",
        "server.json",
        "slack-app-bootstrap-manifest.json",
        "slack-app-manifest.json",
        "knowledge-policy.json",
        "knowledge-control/identity.json",
    }:
        return CONFIG / name
    raise RuntimeError("invalid_setup_checkpoint")


def finish_setup() -> None:
    """Replay only this setup's exact writes; preserve intervening operator edits."""
    pending = CONFIG / "setup-pending.json"
    value = cast("dict[str, dict[str, str | None]]", json.loads(pending.read_text()))
    for name, content in value["files"].items():
        target = setup_target(name)
        if not isinstance(content, str) or target.is_symlink():
            raise RuntimeError("invalid_setup_checkpoint")
        current = target.read_text() if target.exists() else None
        if current == content:
            continue
        if current != value["previous"][name]:
            raise RuntimeError(f"setup_resume_preserves_operator_edit:{name}")
        private_write(target, content)
    (ROOT / "knowledge").mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = initialize_knowledge_store(
        KnowledgeSettings(
            root=ROOT / "knowledge",
            control_root=CONFIG / "knowledge-control",
            policy_path=CONFIG / "knowledge-policy.json",
        )
    )
    _ = EraseLedger(CONFIG / "knowledge-control").initialize()
    pending.unlink()
    typer.echo("설정 완료. 다음: trace-marketing server start")
    typer.echo(f"Slack 최종 App Manifest: {CONFIG / 'slack-app-manifest.json'}")


@app.command("setup")
def setup() -> None:
    """Interactively validate Slack credentials and prepare private settings and user services."""
    try:
        setup_config()
    except Exception as error:  # noqa: BLE001 - secret-bearing requests must never reach tracebacks.
        typer.echo(
            str(error) if isinstance(error, RuntimeError) else type(error).__name__, err=True
        )
        raise typer.Exit(1) from None


@app.command("manifest")
def manifest(
    origin: Annotated[str, typer.Option(help="Public HTTPS origin for Slack callbacks.")],
    bootstrap: Annotated[bool, typer.Option(help="Omit Events until the server is ready.")] = False,
) -> None:
    """Print a Slack App Manifest to paste into Slack, before setup or after startup."""
    name = "slack-app-bootstrap-manifest.json" if bootstrap else "slack-app-manifest.json"
    typer.echo(
        (resources() / name)
        .read_text()
        .replace("https://marketing-agent.borca.ai", origin_value(origin))
    )


def operator_settings() -> dict[str, object]:
    if (CONFIG / "setup-pending.json").exists() or not (CONFIG / "server.json").is_file():
        typer.echo("설정을 먼저 완료하세요: trace-marketing server setup", err=True)
        raise typer.Exit(1)
    return cast("dict[str, object]", json.loads((CONFIG / "server.json").read_text()))


@app.command("start")
def start() -> None:
    """Enable the agent, optional dedicated Tunnel, and five-minute updater."""
    settings = operator_settings()
    _ = execute("systemctl", "--user", "daemon-reload")
    units = [SERVICE, TIMER, *([TUNNEL] if settings["tunnel"] else [])]
    _ = execute("systemctl", "--user", "enable", "--now", *units)
    typer.echo("서비스 시작 요청 완료. trace-marketing server status 로 확인하세요.")
    linger = execute("loginctl", "show-user", str(os.getuid()), "--property=Linger", "--value")
    if linger != "yes":
        typer.echo(
            f"자동 시작에 필요한 관리자 명령: sudo loginctl enable-linger {getpass.getuser()}"
        )


@app.command("stop")
def stop() -> None:
    """Stop the updater and owned services. Startup enablement remains unchanged."""
    settings = operator_settings()
    _ = execute("systemctl", "--user", "stop", TIMER)
    _ = execute("systemctl", "--user", "stop", "trace-marketing-update.service")
    _ = execute("systemctl", "--user", "stop", SERVICE, *([TUNNEL] if settings["tunnel"] else []))


@app.command("github-setup")
def github_setup() -> None:
    """Store an issue-only GitHub credential without changing Slack configuration."""
    try:
        token = validate_token(getpass.getpass("GitHub 토큰 (ads-booster Issues: write): ").strip())
        GitHubIssues(token).check_access()
        private_write(CONFIG / "github.token", token + "\n")
    except GitHubRejectedError as error:
        typer.echo(
            f"GitHub credential rejected (HTTP {error.code}); previous token preserved.", err=True
        )
        raise typer.Exit(1) from None
    except Exception:  # noqa: BLE001 - credential entry must never render traceback locals.
        typer.echo(
            "GitHub setup failed; check token, repository access and file permissions.", err=True
        )
        raise typer.Exit(1) from None
    typer.echo(
        "Token saved; repository accessible. Issues: write permission is required for creation."
    )
    typer.echo("Restart the idle agent: systemctl --user restart trace-marketing.service")


def server_port(settings: dict[str, object]) -> int:
    port = settings.get("port", 8090)
    if type(port) is not int or not 1 <= port <= MAX_PORT:
        raise RuntimeError("server_port_requires_integer_1_to_65535")
    return port


@app.command("status")
def status() -> None:
    """Report local/public health, service and linger status without reading secrets."""
    settings = operator_settings()
    checks: dict[str, object] = {}
    for label, url in [
        ("local", f"http://127.0.0.1:{server_port(settings)}/health"),
        ("public", str(settings["origin"]) + "/health"),
    ]:
        try:
            value = json_request(url)
            checks[label] = {
                k: value.get(k) for k in ["owner", "release", "maintenance", "update_protocol"]
            }
        except Exception:  # noqa: BLE001 - public failures can contain response secrets.
            checks[label] = "unreachable"
    for unit in [SERVICE, TIMER, *([TUNNEL] if settings["tunnel"] else [])]:
        try:
            checks[unit] = execute("systemctl", "--user", "is-active", unit)
        except RuntimeError:
            checks[unit] = "inactive"
    for name in [
        "current/release.json",
        "last-check.json",
        "last-success.json",
        "last-failure.json",
        "transaction.json",
    ]:
        path = ROOT / name
        checks[name] = json.loads(path.read_text()) if path.is_file() else None
    checks["linger"] = execute(
        "loginctl", "show-user", str(os.getuid()), "--property=Linger", "--value"
    )
    typer.echo(json.dumps(checks, indent=2))


@app.command("doctor")
def doctor() -> None:
    """Read-only prerequisites and configuration checks; use status for live health."""
    checks: dict[str, object] = {
        "linux": sys.platform == "linux",
        "managed_main": (ROOT / "current/source").is_dir(),
    }
    for name in ["python3", "uv", "git", "codex", "systemctl"]:
        checks[name] = shutil.which(name) is not None
    checks["configured"] = (CONFIG / "server.json").is_file()
    checks["setup_complete"] = not (CONFIG / "setup-pending.json").exists()
    if checks["configured"] and checks["setup_complete"] and operator_settings().get("tunnel"):
        checks["cloudflared"] = shutil.which("cloudflared") is not None
    try:
        _ = execute("codex", "login", "status")
        checks["codex_login"] = True
    except OSError, RuntimeError, subprocess.TimeoutExpired:
        checks["codex_login"] = False
    checks["ready"] = all(checks.values())
    checks["next"] = (
        "trace-marketing server start"
        if checks["ready"]
        else "Run installer, codex login --device-auth, then trace-marketing server setup."
    )
    typer.echo(json.dumps(checks, indent=2))
    if not checks["ready"]:
        raise typer.Exit(1)


@app.command("update")
def update() -> None:
    """Request a verified-main update through the systemd unit (including its configured PATH)."""
    _ = execute("systemctl", "--user", "start", "--no-block", "trace-marketing-update.service")
    typer.echo("업데이트 검사 요청 완료. trace-marketing server status 로 적용 SHA를 확인하세요.")
