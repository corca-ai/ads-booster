"""Unrelated threads must progress while the first model call is blocked."""

from __future__ import annotations

from threading import Event, Thread
from typing import TYPE_CHECKING, override

from ads_booster.bootstrap.channel_setup import run_slack_worker
from tests.marketing.channels.test_slack_events import RecordingReasoning, receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult


def test_parallel_threads_preserve_same_thread_order_and_shutdown(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    entered, release, second, followup, stop = (Event() for _ in range(5))

    class BlockingReasoning(RecordingReasoning):
        @override
        def plan(self, request: ReasoningRequest) -> ReasoningResult:
            text = request.current_user_message
            if text == "first":
                entered.set()
                assert release.wait(10)
            elif text == "second":
                second.set()
            elif text == "followup":
                followup.set()
            return super().plan(request)

    owner.commands.application.service.reasoning = BlockingReasoning()
    receive(owner, text="<@UBOT> first")
    worker = Thread(target=run_slack_worker, args=(owner.commands, stop, None, owner))
    worker.start()
    try:
        assert entered.wait(3)
        receive(owner, text="<@UBOT> second", ts="200.001")
        receive(owner, type="message", text="followup", ts="100.002", thread_ts="100.001")
        assert second.wait(3), "An unrelated thread was blocked by the first model call"
        assert not followup.is_set()
        release.set()
        assert followup.wait(3)
    finally:
        release.set()
        stop.set()
        worker.join(12)
    assert not worker.is_alive()
    assert {str(message["thread_ts"]) for message in messages if "thread_ts" in message} == {
        "100.001",
        "200.001",
    }
