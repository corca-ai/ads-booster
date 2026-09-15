from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from pydantic import TypeAdapter

from ads_booster.contracts.agent_schedule import AgentSchedule, ScheduleOccurrence
from ads_booster.contracts.threads import ThreadsPublicationReceipt
from ads_booster.contracts.tool_handoff import ToolInputHandoff
from ads_booster.threads.drafts import ThreadsDraftBatch

if TYPE_CHECKING:
    from ads_booster.agent.service.completion_evidence import BoundCompletionEvidence
    from ads_booster.contracts.agent_run import AgentRun
    from ads_booster.tools.completion_registry import CompletionArtifactOwners

_OAUTH_ROW: TypeAdapter[tuple[str, str, str, int] | None] = TypeAdapter(
    tuple[str, str, str, int] | None
)


@dataclass(frozen=True, slots=True)
class ScheduleControlProof:
    def verify(
        self, run: AgentRun, bound: BoundCompletionEvidence, owners: CompletionArtifactOwners
    ) -> bool:
        repository = owners.schedules
        schedules = bound.output.get("schedules")
        occurrence_payloads = bound.output.get("occurrences")
        if repository is None or run.tenant_id != bound.invocation.tenant_id:
            return False
        if not isinstance(schedules, list) or not isinstance(occurrence_payloads, list):
            return False
        if not schedules:
            return bound.invocation.input.get("action") == "list"
        parsed_schedules: list[AgentSchedule] = []
        for payload in schedules:
            if not isinstance(payload, dict):
                return False
            schedule_payload = {
                key: value for key, value in payload.items() if key != "next_occurrence"
            }
            schedule = AgentSchedule.model_validate(schedule_payload)
            if repository.get(run.tenant_id, schedule.schedule_id) != schedule:
                return False
            parsed_schedules.append(schedule)
        occurrences = tuple(
            ScheduleOccurrence.model_validate(payload) for payload in occurrence_payloads
        )
        if bound.invocation.input.get("action") != "occurrences":
            return not occurrences
        if len(parsed_schedules) != 1:
            return False
        limit = bound.invocation.input.get("limit", 100)
        return (
            isinstance(limit, int)
            and repository.occurrences(parsed_schedules[0].schedule_id, limit=limit) == occurrences
        )


@dataclass(frozen=True, slots=True)
class ThreadsDraftControlProof:
    def verify(
        self, run: AgentRun, bound: BoundCompletionEvidence, owners: CompletionArtifactOwners
    ) -> bool:
        repository = owners.threads_drafts
        publications = owners.threads_publications
        payload = bound.output.get("batch")
        publication_payloads = bound.output.get("publications")
        if (
            repository is None
            or publications is None
            or not isinstance(payload, dict)
            or not isinstance(publication_payloads, list)
        ):
            return False
        batch = ThreadsDraftBatch.model_validate(payload)
        receipts = tuple(
            ThreadsPublicationReceipt.model_validate(item) for item in publication_payloads
        )
        return (
            batch.workspace_id == run.tenant_id
            and repository.get(batch.workspace_id, batch.batch_id) == batch
            and publications.list_for_batch(batch.batch_id) == receipts
        )


@dataclass(frozen=True, slots=True)
class ThreadsAccountControlProof:
    def verify(
        self, run: AgentRun, bound: BoundCompletionEvidence, owners: CompletionArtifactOwners
    ) -> bool:
        repository = owners.threads_accounts
        connection_id = bound.output.get("connection_id")
        if repository is None or not isinstance(connection_id, str):
            return False
        account = repository.get(run.tenant_id, connection_id)
        if account is None:
            return False
        expected = {
            "connection_id": account.connection_id,
            "username": account.username,
            "country": account.country,
            "concept": account.concept,
            "tone": account.tone,
            "references": [item.model_dump(mode="json") for item in account.references],
            "status": account.status,
            "expires_at": account.expires_at.isoformat(),
        }
        return all(bound.output.get(key) == value for key, value in expected.items())


@dataclass(frozen=True, slots=True)
class ThreadsConnectControlProof:
    def input_handoff(
        self,
        run: AgentRun,
        bound: BoundCompletionEvidence,
        owners: CompletionArtifactOwners,
        now: datetime,
    ) -> ToolInputHandoff | None:
        if not self.verify(run, bound, owners) or owners.database_path is None:
            return None
        url = str(bound.output["authorization_url"])
        expiry = str(bound.output["expires_at"])
        try:
            expires_at = datetime.fromisoformat(expiry)
        except ValueError:
            return None
        if expires_at.tzinfo is None:
            return None
        state = parse_qs(urlsplit(url).query)["state"][0]
        with closing(sqlite3.connect(owners.database_path)) as database:
            row = _OAUTH_ROW.validate_python(
                database.execute(
                    """SELECT workspace_id,expires_at,redirect_uri,consumed
                    FROM threads_oauth_states WHERE state_id=?""",
                    (state,),
                ).fetchone()
            )
        if row is None:
            return None
        question = (
            "\n".join(
                (
                    "Threads 연결 승인이 필요합니다. 아래 링크에서 승인한 뒤 이 대화에 알려주세요.",
                    url,
                    f"만료 시각: {expiry}",
                    "아직 계정 연결 완료를 확인하지 않았습니다.",
                )
            )
            if expires_at > now
            else "만료된 링크입니다. 승인했다면 알려주세요. 아니면 새 대화에서 연결을 요청하세요."
        )
        if row[3] == 1:
            question = "사용된 링크입니다. 승인을 마쳤다면 알려주세요. 연결 상태 확인이 필요합니다."
        return ToolInputHandoff(
            question=question,
            expected_outcome="User completes Threads OAuth consent and reports back",
        )

    def verify(
        self, run: AgentRun, bound: BoundCompletionEvidence, owners: CompletionArtifactOwners
    ) -> bool:
        database_path = owners.database_path
        authorization_url = bound.output.get("authorization_url")
        expires_at = bound.output.get("expires_at")
        if database_path is None or not isinstance(authorization_url, str):
            return False
        if not isinstance(expires_at, str):
            return False
        url = urlsplit(authorization_url)
        state = parse_qs(url.query).get("state", [""])[0]
        if not state:
            return False
        with closing(sqlite3.connect(database_path)) as database, database:
            row = _OAUTH_ROW.validate_python(
                database.execute(
                    """SELECT workspace_id,expires_at,redirect_uri,consumed
                    FROM threads_oauth_states WHERE state_id=?""",
                    (state,),
                ).fetchone()
            )
        return (
            row is not None
            and row[0] == run.tenant_id
            and row[1] == expires_at
            and url.scheme == "https"
            and url.netloc == "threads.net"
            and url.path == "/oauth/authorize"
            and not url.fragment
            and row[2] in parse_qs(urlsplit(authorization_url).query).get("redirect_uri", [])
            and row[3] in {0, 1}
        )


__all__ = [
    "ScheduleControlProof",
    "ThreadsAccountControlProof",
    "ThreadsConnectControlProof",
    "ThreadsDraftControlProof",
]
