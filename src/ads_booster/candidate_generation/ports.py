"""The protocols candidate generation depends on, stated without any kernel type.

Everything here is deliberately free of `agent/runs` and `connectors/` types. The engine,
the background judge, the local composition, and the Web layer program against these; only
`candidate_generation/kernel/` names the durable Agent runtime behind them. That is what
lets the execution kernel be replaced without touching the code that produces candidates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from ads_booster.agent.session import ModelClient
    from ads_booster.candidate_generation.models import CandidateBatch
    from ads_booster.workspace import (
        CandidateCreate,
        CandidateId,
        CandidateImageAttachment,
        CandidateRecord,
        MarketingAccountRecord,
        WorkspaceId,
    )


class CandidateModelSource(Protocol):
    def open(self) -> AbstractContextManager[ModelClient]: ...


class CandidateCreator(Protocol):
    def create_candidate(self, value: CandidateCreate) -> CandidateRecord: ...


class CandidateGeneratorPort(Protocol):
    def generate(
        self,
        workspace_id: WorkspaceId,
        *,
        run_context: str | None = None,
        account: MarketingAccountRecord | None = None,
    ) -> CandidateBatch: ...


class CandidateImageRunnerPort(Protocol):
    def generate(self, workspace_id: WorkspaceId, candidate_id: CandidateId) -> CandidateRecord: ...


class CandidateImageStore(Protocol):
    def get_candidate(
        self, workspace_id: WorkspaceId, candidate_id: CandidateId
    ) -> CandidateRecord: ...

    def attach_candidate_image(
        self,
        workspace_id: WorkspaceId,
        candidate_id: CandidateId,
        attachment: CandidateImageAttachment,
    ) -> CandidateRecord: ...


class ImageReviewPort(Protocol):
    """Carries one human image decision to whatever executed the run.

    The workflow knows a candidate has a run id and that a decision has to reach it. It
    does not know that the run is a durable Agent run, what state machine it is in, or how
    a rejection resumes it — `kernel/image_stage.py` owns all of that.
    """

    def review(
        self,
        agent_run_id: str,
        *,
        accepted: bool,
        note: str | None,
        at: float,
    ) -> None: ...
