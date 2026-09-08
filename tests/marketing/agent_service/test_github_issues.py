from __future__ import annotations

import json
from dataclasses import dataclass
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TYPE_CHECKING, override
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from ads_booster.marketing.agent_service.github_issues import (
    API,
    CAPABILITY,
    REPOSITORY,
    GitHubIssues,
    descriptor,
    open_github,
    token_from_env,
)
from ads_booster.marketing.agent_service.integrations import (
    AgentServiceIntegrationConfig,
    ConfiguredAgentTools,
)
from tests.marketing.agent_service.test_integrations import (
    NOW,
    UnusedResearchRunner,
    _invocation,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

PAYLOAD: JsonObject = {
    "repository": REPOSITORY,
    "title": "오류 수정 요청",
    "body": "재현 단계와 기대 결과",
}
URL = f"https://github.com/{REPOSITORY}/issues/123"


@dataclass
class Response:
    payload: JsonObject
    status: int = 200

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()

    def close(self) -> None:
        pass


def test_issue_posts_only_approved_fields_and_reads_back_exact_issue() -> None:
    requests: list[Request] = []

    def opener(request: Request, *, timeout: float) -> Response:
        assert timeout == 30
        requests.append(request)
        assert request.get_header("Authorization") == "Bearer fixture_token"
        return Response(
            {"number": 123, "html_url": URL, "title": PAYLOAD["title"], "body": PAYLOAD["body"]},
            201 if request.method == "POST" else 200,
        )

    tool = GitHubIssues("fixture_token", opener)
    desc = descriptor(now=NOW)
    result = tool.execute(_invocation(desc, PAYLOAD), desc)
    assert result.disposition == "succeeded"
    assert result.output == {"repository": REPOSITORY, "number": 123, "url": URL}
    assert [r.full_url for r in requests] == [API + "/issues", API + "/issues/123"]
    data = requests[0].data
    assert isinstance(data, bytes)
    assert json.loads(data) == {
        "title": PAYLOAD["title"],
        "body": PAYLOAD["body"],
    }
    assert "fixture_token" not in repr(tool)


@pytest.mark.parametrize(
    "change",
    [
        {"repository": "other/repo"},
        {"title": " "},
        {"body": ""},
        {"labels": ["bug"]},
        {"url": "https://evil.example"},
    ],
)
def test_invalid_issue_never_contacts_github(change: JsonObject) -> None:
    def opener(*_args: object, **_kwargs: object) -> Response:
        pytest.fail("invalid input made a network request")

    desc = descriptor(now=NOW)
    with pytest.raises(ValueError, match="validation error"):
        _ = GitHubIssues("fixture_token", opener).execute(
            _invocation(desc, {**PAYLOAD, **change}), desc
        )


@pytest.mark.parametrize("failure", ["post_timeout", "read_timeout", "wrong_url", "wrong_body"])
def test_uncertain_or_rejected_creation_never_reposts_or_returns_success(failure: str) -> None:
    requests: list[Request] = []

    def opener(request: Request, *, timeout: float) -> Response:
        _ = timeout
        requests.append(request)
        if failure == "post_timeout" or (failure == "read_timeout" and len(requests) == 2):
            message = "private-token-in-message"
            raise TimeoutError(message)
        return Response(
            {
                "number": 123,
                "html_url": "https://evil.example" if failure == "wrong_url" else URL,
                "title": PAYLOAD["title"],
                "body": "changed" if failure == "wrong_body" else PAYLOAD["body"],
            },
            201 if request.method == "POST" else 200,
        )

    desc = descriptor(now=NOW)
    with pytest.raises(ValueError, match="github_") as error:
        _ = GitHubIssues("fixture_token", opener).execute(_invocation(desc, PAYLOAD), desc)
    assert "private-token" not in str(error.value)
    assert sum(r.method == "POST" for r in requests) == 1


def test_credential_is_optional_private_and_not_in_catalog(tmp_path: Path) -> None:
    assert (
        CAPABILITY
        not in ConfiguredAgentTools(
            AgentServiceIntegrationConfig(), UnusedResearchRunner()
        ).adapters()
    )
    path = tmp_path / "github.token"
    _ = path.write_text("fixture_token\n")
    path.chmod(0o600)
    token = token_from_env({"TRACE_MARKETING_GITHUB_TOKEN_FILE": str(path)})
    assert token == "fixture_token"  # noqa: S105 - fixture credential
    configured = ConfiguredAgentTools(
        AgentServiceIntegrationConfig(github_token=token), UnusedResearchRunner()
    )
    assert CAPABILITY in configured.adapters()
    catalog = configured.descriptors(now=NOW)
    assert "fixture_token" not in repr(configured.config)
    assert all("fixture_token" not in item.model_dump_json() for item in catalog)
    assert (
        next(item for item in catalog if item.capability_id == CAPABILITY).approval_policy.mode
        == "required"
    )
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private_regular_file"):
        _ = token_from_env({"TRACE_MARKETING_GITHUB_TOKEN_FILE": str(path)})


def test_permission_rejection_returns_sanitized_failure_without_retry() -> None:
    calls: list[Request] = []

    def opener(request: Request, *, timeout: float) -> Response:
        _ = timeout
        calls.append(request)
        raise HTTPError(API, 403, "private-provider-message", Message(), None)

    desc = descriptor(now=NOW)
    result = GitHubIssues("fixture_token", opener).execute(_invocation(desc, PAYLOAD), desc)
    assert result.disposition == "failed"
    assert result.output == {"repository": REPOSITORY, "error": "github_http_403"}
    assert len(calls) == 1


def test_http_redirect_does_not_forward_credential() -> None:
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/forbidden")
            self.end_headers()

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/start",
            headers={"Authorization": "Bearer fixture"},
        )
        with pytest.raises(HTTPError, match="302") as error:
            _ = open_github(request, timeout=2)
        error.value.close()
        assert seen == ["/start"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
