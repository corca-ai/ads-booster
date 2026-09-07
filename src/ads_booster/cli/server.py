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
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import typer

if TYPE_CHECKING:
    from http.client import HTTPResponse

app = typer.Typer(no_args_is_help=True, help="Install settings and operate the Linux Slack agent.")
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
    if path.is_symlink():
        raise RuntimeError("refusing_symlink")
    temp = path.with_suffix(".new")
    # Exclusive creation avoids following a pre-existing temporary symlink.
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            _ = stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        _ = temp.replace(path)
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
    paths = list(
        dict.fromkeys(str(Path(executable(n)).parent) for n in ["uv", "git", "gh", "codex"])
    )
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
    setup_preflight()
    origin = origin_value(prompt("공개 HTTPS 주소 (예: https://marketing-agent.borca.ai)"))
    model = prompt("이 서버 Codex에서 사용할 모델명")
    tenant = prompt("워크스페이스 이름", default="corca-marketing")
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
    private_write(CONFIG / "slack-installation.json", json.dumps(installation, indent=2))
    private_write(CONFIG / "agent.env", environment)
    if tunnel:
        private_write(CONFIG / "tunnel.token", tunnel_token)
    for name, content in units.items():
        private_write(UNITS / name, content)
    private_write(CONFIG / "server.json", json.dumps({"origin": origin, "tunnel": tunnel}))
    for name in ["slack-app-bootstrap-manifest.json", "slack-app-manifest.json"]:
        value = (resources() / name).read_text().replace("https://marketing-agent.borca.ai", origin)
        private_write(CONFIG / name, value)
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


@app.command("status")
def status() -> None:
    """Report local/public health, service and linger status without reading secrets."""
    settings = operator_settings()
    checks: dict[str, object] = {}
    for label, url in [
        ("local", "http://127.0.0.1:8765/health"),
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
    for name in ["python3", "uv", "git", "gh", "codex", "systemctl", "cloudflared"]:
        checks[name] = shutil.which(name) is not None
    checks["configured"] = (CONFIG / "server.json").is_file()
    typer.echo(json.dumps(checks, indent=2))


@app.command("update")
def update() -> None:
    """Request a verified-main update through the systemd unit (including its configured PATH)."""
    _ = execute("systemctl", "--user", "start", "--no-block", "trace-marketing-update.service")
    typer.echo("업데이트 검사 요청 완료. trace-marketing server status 로 적용 SHA를 확인하세요.")
