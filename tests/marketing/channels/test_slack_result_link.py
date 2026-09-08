"""Same-work results use authenticated Web readback without exposing private Runs."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

import pytest

from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("public_links", [True, False])
def test_shared_summary_links_same_run_only_when_web_is_enabled(
    tmp_path: Path, public_links: bool
) -> None:
    events, messages = setup_events(tmp_path)
    events.commands.public_links = public_links
    receive(events)
    assert events.work_once(now=NOW)
    run = events.commands.application.service.repository.list_runs("team")[0]
    url = events.commands.application.result_base_url + "/runs/" + quote(run.run_id, safe="")
    assert (url in str(messages[-1])) is public_links


def test_private_summary_does_not_offer_shared_web_projection(tmp_path: Path) -> None:
    events, messages = setup_events(tmp_path)
    receive(events, type="message", channel="D1", channel_type="im", text="개인 질문")
    assert events.work_once(now=NOW)
    assert events.commands.application.result_base_url not in str(messages[-1])
