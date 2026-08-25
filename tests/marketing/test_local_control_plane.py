from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from trace_capture.marketing.simulator import (
    LiveAdapterUnavailableError,
    LocalMarketingControlPlane,
    MarketingAccount,
    RunState,
)


def test_account_loop_pauses_for_approval_then_commits_only_private_memory(tmp_path: Path) -> None:
    control = LocalMarketingControlPlane(tmp_path)
    _ = control.register_account(MarketingAccount(account_id="trace_kr", country="KR"))
    _ = control.register_account(MarketingAccount(account_id="trace_us", country="US"))

    paused = control.start_run("trace_kr")

    assert paused.state is RunState.AWAITING_HUMAN_APPROVAL
    assert control.memories("trace_kr") == ()
    completed = control.approve(paused.run_id)
    assert completed.state is RunState.COMPLETED
    assert completed.publication_id is not None
    assert len(control.memories("trace_kr")) == 1
    assert control.memories("trace_us") == ()


def test_live_adapter_requires_capability_probe_instead_of_simulating_publish(
    tmp_path: Path,
) -> None:
    control = LocalMarketingControlPlane(tmp_path)
    _ = control.register_account(
        MarketingAccount(
            account_id="trace_live",
            country="KR",
            adapter_mode="live",
            credential_ref="keychain:threads/trace_live",
        )
    )

    with pytest.raises(LiveAdapterUnavailableError, match="capability probe"):
        _ = control.start_run("trace_live", auto_approve=True)
