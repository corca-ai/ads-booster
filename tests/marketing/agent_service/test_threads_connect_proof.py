from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.tools.completion_proofs import CompletionArtifactOwners, configured_proof_registry
from ads_booster.tools.threads_connection import descriptor
from tests.marketing.agent_service.completion_fixtures import NOW
from tests.marketing.agent_service.test_completion_proof_registry import issue_evidence
from tests.marketing.agent_service.test_task_progress import make_run

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


@pytest.mark.parametrize(
    "url",
    [
        "https://threads.net/oauth/authorize",
        "http://threads.net/oauth/authorize",
        "https://threads.net/other",
        "https://threads.net.evil.example/oauth/authorize",
        "https://attacker@threads.net/oauth/authorize",
        "https://threads.net:443/oauth/authorize",
    ],
)
@pytest.mark.parametrize("workspace", ["trace", "other"])
def test_connect_proof_requires_exact_provider_endpoint_and_workspace(
    tmp_path: Path, url: str, workspace: str
) -> None:
    # Given a durable OAuth state and canonical invocation/receipt bindings.
    database = tmp_path / "oauth.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        _ = connection.execute(
            """CREATE TABLE threads_oauth_states (
                state_id TEXT,workspace_id TEXT,expires_at TEXT,redirect_uri TEXT,consumed INTEGER
            )"""
        )
        _ = connection.execute(
            "INSERT INTO threads_oauth_states VALUES(?,?,?,?,?)",
            ("state", workspace, NOW.isoformat(), "https://agent.example/callback", 0),
        )
    original = issue_evidence()
    tool = descriptor(now=NOW)
    invocation = original.invocation.model_copy(
        update={
            "tenant_id": "trace",
            "descriptor_sha256": contract_sha256(tool),
        }
    )
    output: JsonObject = {
        "authorization_url": url
        + "?state=state&redirect_uri=https%3A%2F%2Fagent.example%2Fcallback",
        "expires_at": NOW.isoformat(),
    }
    bound = replace(
        original,
        invocation=invocation,
        descriptor=tool,
        output=output,
        receipt=original.receipt.model_copy(
            update={
                "executor_id": "threads-oauth",
                "invocation_sha256": contract_sha256(invocation),
                "output_sha256": contract_sha256(output),
            }
        ),
    )
    # When the configured owner verifies the issued link.
    verified = configured_proof_registry().verify(
        make_run().model_copy(update={"tenant_id": "trace"}),
        bound,
        CompletionArtifactOwners(database_path=database),
    )
    # Then only the exact provider endpoint and owning tenant are accepted.
    assert verified is (url == "https://threads.net/oauth/authorize" and workspace == "trace")
