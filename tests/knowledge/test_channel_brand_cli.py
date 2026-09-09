from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter
from typer.testing import CliRunner

from ads_booster.cli.knowledge import app
from ads_booster.knowledge.configuration import (
    KnowledgeSettings,
    LocalKnowledgePolicy,
    initialize_knowledge_store,
    load_local_actor,
)
from ads_booster.knowledge.contracts import (
    ActorContext,
    Brand,
    GrantCapability,
    OperationReceipt,
    ScopeKind,
)
from ads_booster.knowledge.repository import SqliteKnowledgeRepository
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.tool_contracts import MemoryData, ToolResult, ToolResultStatus

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import Result

    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class ChannelBrandCli:
    settings: KnowledgeSettings

    @property
    def options(self) -> list[str]:
        root, control, policy = self.settings.require_enabled()
        return ["--root", str(root), "--control-root", str(control), "--policy", str(policy)]

    def invoke(self, *command: str) -> Result:
        return CliRunner().invoke(app, [*command, *self.options])

    def register(self, **extra: str) -> Result:
        root, _, _ = self.settings.require_enabled()
        request: JsonObject = {"operation_id": "operation.same", "name": "Same Name", **extra}
        path = root / "request.json"
        _ = path.write_text(json.dumps(request))
        return self.invoke("brand", "register", "--request", str(path))

    def channel(self, channel_id: str) -> ChannelBrandCli:
        root, control, policy = self.settings.require_enabled()
        document = LocalKnowledgePolicy.model_validate_json(policy.read_bytes()).model_dump(
            mode="json", by_alias=True
        )
        document["channel_id"] = channel_id
        path = control / f"policy-{channel_id}.json"
        _ = path.write_text(json.dumps(document))
        path.chmod(0o600)
        return ChannelBrandCli(KnowledgeSettings(root=root, control_root=control, policy_path=path))


@pytest.fixture
def cli(tmp_path: Path) -> ChannelBrandCli:
    local = ChannelBrandCli(
        KnowledgeSettings(
            root=tmp_path / "store",
            control_root=tmp_path / "control",
            policy_path=tmp_path / "control/policy.json",
        )
    )
    initialized = local.invoke("init", "--workspace", "workspace.cli")
    assert initialized.exit_code == 0, initialized.output
    return local


def test_channel_policy_routes_same_named_brands_and_soul(cli: ChannelBrandCli) -> None:
    # Given separate trusted policy copies sharing one store, and an unchanged workspace policy.
    _, _, original_policy = cli.settings.require_enabled()
    original = original_policy.read_bytes()
    clients = (cli, cli.channel("CA"), cli.channel("CB"))
    receipts: list[OperationReceipt] = []
    # When operators issue the same registration request in each configured scope.
    for client in clients:
        result = client.register()
        assert result.exit_code == 0, result.output
        receipt = OperationReceipt.model_validate_json(result.output)
        receipts.append(receipt)
        replay = client.register()
        assert replay.exit_code == 0, replay.output
        assert OperationReceipt.model_validate_json(replay.output) == receipt
    # Then registration, listing, direct lookup and SOUL selection remain scope-local.
    assert len({receipt.operation_id for receipt in receipts}) == 3
    assert len({receipt.resulting_revision_ids[0] for receipt in receipts}) == 3
    root, _, _ = cli.settings.require_enabled()
    repository = SqliteKnowledgeRepository(root)
    for client, receipt in zip(clients, receipts, strict=True):
        actor = load_local_actor(client.settings)
        listed = client.invoke("brand", "list")
        assert listed.exit_code == 0, listed.output
        brands = TypeAdapter(tuple[Brand, ...]).validate_json(listed.output)
        own_id = receipt.resulting_revision_ids[0]
        assert tuple(brand.brand_id for brand in brands) == (own_id,)
        assert brands[0].owned_scope == actor.conversation_scope
        for candidate in receipts:
            brand_id = candidate.resulting_revision_ids[0]
            result = client.invoke("memory", "get", "--kind", "soul", "--brand", brand_id)
            assert result.exit_code == 0, result.output
            memory = ToolResult.model_validate_json(result.output)
            if brand_id == own_id:
                assert repository.brand(actor, brand_id) is not None
                assert isinstance(memory.data, MemoryData)
                assert memory.data.document.owned_scope == actor.conversation_scope
            else:
                assert repository.brand(actor, brand_id) is None
                assert memory.status is ToolResultStatus.NOT_FOUND
    assert original_policy.read_bytes() == original
    assert load_local_actor(cli.settings).conversation_scope.kind is ScopeKind.WORKSPACE


def test_channel_policy_namespaces_initial_memories_and_voice_grants(cli: ChannelBrandCli) -> None:
    # Given channel policies with an explicit operator-granted brand voice ID.
    clients = (cli.channel("CA"), cli.channel("CB"))
    actors: list[ActorContext] = []
    for client in clients:
        registration = client.register()
        assert registration.exit_code == 0, registration.output
        brand_id = OperationReceipt.model_validate_json(registration.output).resulting_revision_ids[
            0
        ]
        _, _, path = client.settings.require_enabled()
        policy = LocalKnowledgePolicy.model_validate_json(path.read_bytes())
        _ = path.write_text(
            policy.model_copy(update={"brand_voice_brand_ids": (brand_id,)}).model_dump_json(
                by_alias=True
            )
        )
        # When initializing through the configured actor, then each channel has its own memory IDs.
        actor = initialize_knowledge_store(client.settings)
        actors.append(actor)
        root, _, _ = client.settings.require_enabled()
        stored = SqliteKnowledgeRepository(root).read_memory(
            actor, f"memory.core.{scope_key(actor.conversation_scope)}"
        )
        assert stored is not None
        assert stored.document.owned_scope == actor.conversation_scope
        voice = next(
            grant for grant in actor.grants if grant.capability is GrantCapability.BRAND_VOICE_EDIT
        )
        assert voice.scope == actor.conversation_scope
    assert actors[0].session_id != actors[1].session_id
    assert not (
        {grant.grant_id for grant in actors[0].grants}
        & {grant.grant_id for grant in actors[1].grants}
    )


def test_request_cannot_override_configured_channel(cli: ChannelBrandCli) -> None:
    # Given trusted channel A policy, when the request claims B, then it cannot switch scope.
    result = cli.channel("CA").register(channel_id="CB")
    assert result.exit_code != 0
    assert str(result.exception) == "knowledge_brand_scope_claim_rejected"
