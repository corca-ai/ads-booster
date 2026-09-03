from __future__ import annotations

import plistlib
from pathlib import Path

from ads_booster.marketing.agent_service.launchd import MarketingAgentLaunchd


def test_install_keeps_generated_bearer_token_out_of_launchd_plist(tmp_path: Path) -> None:
    home = tmp_path / "state"
    plist = tmp_path / "LaunchAgents" / "agent.plist"
    launchd = MarketingAgentLaunchd(
        executable=Path("/opt/trace/bin/trace-marketing"),
        agent_home=home,
        plist_path=plist,
        codex_executable=Path("/opt/homebrew/bin/codex"),
        model="gpt-5.4",
    )

    token = launchd.install()
    payload = plistlib.loads(plist.read_bytes())

    assert launchd.token_path.read_text(encoding="utf-8").strip() == token
    assert launchd.token_path.stat().st_mode & 0o777 == 0o600
    assert token not in plist.read_text(encoding="utf-8")
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    assert payload["ProgramArguments"][1:3] == ["service", "daemon"]
    assert launchd.owns_installed_plist() is True


def test_reinstall_preserves_existing_service_token(tmp_path: Path) -> None:
    launchd = MarketingAgentLaunchd(
        executable=Path("/opt/trace/bin/trace-marketing"),
        agent_home=tmp_path / "state",
        plist_path=tmp_path / "agent.plist",
        codex_executable=Path("/opt/homebrew/bin/codex"),
        model="gpt-5.4",
    )

    assert launchd.install() == launchd.install()
