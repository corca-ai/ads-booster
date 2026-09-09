from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.channels.slack_events import SlackEvents
from ads_booster.contracts.agent_memory import MemoryAccess, MemoryScope, MemorySelection
from ads_booster.learning.memory import SQLiteMemoryStore
from ads_booster.transport.json_types import JsonObject
from tests.marketing.agent_service.test_memory import approve, note
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import RecordingReasoning, receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def test_signed_channel_context_excludes_other_and_legacy_notes_after_restart(
    tmp_path: Path,
) -> None:
    owner, _ = setup_events(tmp_path)
    store = SQLiteMemoryStore(owner.store.database_path)
    for note_id, channel in (("team-a", "C1"), ("team-b", "C2"), ("legacy", None)):
        actor = MemoryAccess(
            scope=MemoryScope(workspace_id="team", product_id="trace", channel_id=channel),
            actor_id="member",
            can_review=True,
        )
        _ = approve(store, note(note_id, actor), actor)
    provider = RecordingReasoning()
    owner.commands.application.service.reasoning = provider
    receive(owner, text="<@UBOT> calendar", channel="C1")
    restarted = SlackEvents(owner.commands, "UBOT", frozenset({"C1", "C2"}))
    restarted.recover()
    assert restarted.work_once(now=NOW)
    receive(restarted, text="<@UBOT> calendar", channel="C2", ts="200.001")
    assert restarted.work_once(now=NOW)

    selections: list[MemorySelection] = []
    for request in provider.requests:
        current = next(
            evidence
            for evidence in request.evidence
            if evidence.get("schema_version") == "trace.current-slack-context.v1"
        )
        memory = _JSON_OBJECT.validate_python(current["memory"])
        selections.append(MemorySelection.model_validate(memory["data"]))
    assert [[item.note_id for item in selection.notes] for selection in selections] == [
        ["team-a"],
        ["team-b"],
    ]
    assert [selection.receipt.scope.channel_id for selection in selections] == ["C1", "C2"]
