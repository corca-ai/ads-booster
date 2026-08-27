from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock
from typing import TYPE_CHECKING
from uuid import uuid4

from ads_booster.agent.tui_approval import PermissionMode

if TYPE_CHECKING:
    from ads_booster.workspace import MemberId, WorkspaceId


@dataclass(frozen=True, slots=True)
class PendingApproval:
    request_id: str
    action: str
    detail: str


@dataclass(slots=True)  # noqa: MUTABLE_OK
class _ApprovalWaiter:
    request: PendingApproval
    event: Event = field(default_factory=Event)
    decision: bool = False


@dataclass(slots=True)  # noqa: MUTABLE_OK
class WebApproval:
    timeout_seconds: float = 300.0
    _mode: PermissionMode = PermissionMode.YOLO
    _lock: Lock = field(default_factory=Lock)
    _pending: _ApprovalWaiter | None = None

    @property
    def mode(self) -> PermissionMode:
        with self._lock:
            return self._mode

    def set_mode(self, mode: PermissionMode) -> None:
        with self._lock:
            self._mode = mode

    def request(self, action: str, detail: str) -> bool:
        with self._lock:
            if self._mode is PermissionMode.YOLO:
                return True
            if self._pending is not None:
                return False
            waiter = _ApprovalWaiter(
                PendingApproval(request_id=uuid4().hex, action=action, detail=detail)
            )
            self._pending = waiter
        if not waiter.event.wait(self.timeout_seconds):
            with self._lock:
                if self._pending is waiter:
                    self._pending = None
            return False
        return waiter.decision

    def pending(self) -> PendingApproval | None:
        with self._lock:
            return None if self._pending is None else self._pending.request

    def resolve(self, request_id: str, *, decision: bool) -> bool:
        with self._lock:
            waiter = self._pending
            if waiter is None or waiter.request.request_id != request_id:
                return False
            waiter.decision = decision
            self._pending = None
            waiter.event.set()
            return True


@dataclass(slots=True)  # noqa: MUTABLE_OK
class WebAgentState:
    model: str
    reasoning: str | None
    approval: WebApproval


@dataclass(frozen=True, slots=True)
class WebAgentStateSnapshot:
    model: str
    reasoning: str | None
    permission_mode: PermissionMode


type MemberAgentKey = tuple[WorkspaceId, MemberId]


@dataclass(slots=True)  # noqa: MUTABLE_OK
class WebAgentStateStore:
    _states: dict[MemberAgentKey, WebAgentState] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def state(
        self,
        workspace_id: WorkspaceId,
        member_id: MemberId,
        default_model: str,
        default_reasoning: str | None = None,
    ) -> WebAgentState:
        key = (workspace_id, member_id)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = WebAgentState(default_model, default_reasoning, WebApproval())
                self._states[key] = state
            return state

    def snapshot(
        self,
        workspace_id: WorkspaceId,
        member_id: MemberId,
        default_model: str,
        default_reasoning: str | None = None,
    ) -> WebAgentStateSnapshot:
        state = self.state(workspace_id, member_id, default_model, default_reasoning)
        return WebAgentStateSnapshot(state.model, state.reasoning, state.approval.mode)

    def set_model(self, workspace_id: WorkspaceId, member_id: MemberId, model: str) -> None:
        with self._lock:
            state = self._states[(workspace_id, member_id)]
            state.model = model

    def set_reasoning(
        self,
        workspace_id: WorkspaceId,
        member_id: MemberId,
        reasoning: str | None,
    ) -> None:
        with self._lock:
            state = self._states[(workspace_id, member_id)]
            state.reasoning = reasoning
