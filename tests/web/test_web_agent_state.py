from __future__ import annotations

from threading import Event, Thread

from trace_capture.agent.tui_approval import PermissionMode
from trace_capture.web.agent_state import WebAgentStateStore, WebApproval
from trace_capture.workspace import MemberId, WorkspaceId


def test_web_approval_resolves_a_pending_browser_decision() -> None:
    approval = WebApproval(timeout_seconds=1.0)
    approval.set_mode(PermissionMode.ASK)
    started = Event()
    decisions: list[bool] = []

    def wait_for_decision() -> None:
        started.set()
        decisions.append(approval.request("shell", "run a command"))

    worker = Thread(target=wait_for_decision)
    worker.start()
    assert started.wait(timeout=1.0)
    pending = next((approval.pending() for _ in range(10_000) if approval.pending()), None)
    assert pending is not None
    assert approval.resolve(pending.request_id, decision=True) is True
    worker.join(timeout=1.0)

    assert decisions == [True]


def test_web_agent_controls_are_isolated_by_member() -> None:
    states = WebAgentStateStore()
    workspace_id = WorkspaceId("workspace")
    ada_id = MemberId("ada")
    grace_id = MemberId("grace")
    ada = states.state(workspace_id, ada_id, "gpt-5.5")
    _ = states.state(workspace_id, grace_id, "gpt-5.5")
    ada.approval.set_mode(PermissionMode.ASK)
    states.set_model(workspace_id, ada_id, "gpt-5.4")

    ada_snapshot = states.snapshot(workspace_id, ada_id, "gpt-5.5")
    grace_snapshot = states.snapshot(workspace_id, grace_id, "gpt-5.5")

    assert ada_snapshot.model == "gpt-5.4"
    assert ada_snapshot.permission_mode is PermissionMode.ASK
    assert grace_snapshot.model == "gpt-5.5"
    assert grace_snapshot.permission_mode is PermissionMode.YOLO
