from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from typing import TYPE_CHECKING, Final
from urllib.parse import parse_qs, urlsplit

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import AgentIntent

_STATE_ROW: Final[TypeAdapter[tuple[int] | None]] = TypeAdapter(tuple[int] | None)

if TYPE_CHECKING:
    from ads_booster.agent.service.task_completion import TaskCompletionService
    from ads_booster.contracts.agent_run import AgentRecord, AgentRun


def threads_connect_handoff(
    run: AgentRun, record: AgentRecord, completion: TaskCompletionService | None, *, now: datetime
) -> AgentIntent | None:
    if (
        record.payload.get("capability_id") != "threads.connect"
        or completion is None
        or completion.proof_reader is None
        or not completion.proof_reader.summarize(run, record).verified
    ):
        return None
    output = record.payload.get("output")
    if not isinstance(output, dict):
        return None
    url, expiry = output.get("authorization_url"), output.get("expires_at")
    if not isinstance(url, str) or not isinstance(expiry, str):
        return None
    try:
        expires_at = datetime.fromisoformat(expiry)
    except ValueError:
        return None
    state = parse_qs(urlsplit(url).query).get("state", [""])[0]
    with closing(sqlite3.connect(completion.repository.database_path)) as database:
        row = _STATE_ROW.validate_python(
            database.execute(
                "SELECT consumed FROM threads_oauth_states WHERE state_id=?", (state,)
            ).fetchone()
        )
    if row is None or expires_at.tzinfo is None:
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
        else "만료된 링크입니다. 승인했으면 알려주세요. 미승인이면 새 대화에서 연결을 요청하세요."
    )
    if row[0] == 1:
        question = "사용된 링크입니다. 승인을 마쳤다면 알려주세요. 연결 상태 확인이 필요합니다."
    return AgentIntent(
        schema_version="trace.agent-intent.v1",
        intent_id=f"{run.run_id}:intent:{run.revision}",
        run_id=run.run_id,
        step_id=f"{run.run_id}:step:{run.revision}",
        action="request_input",
        evidence_sha256s=(record.payload_sha256,),
        expected_outcome="User completes Threads OAuth consent and reports back",
        reasoning_summary=question,
    )
