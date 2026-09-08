"""Approval-bound issue creation for the fixed ads-booster repository."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, cast, override
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import Field, TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.tool_adapters.compatibility import DelegatedToolResult
from ads_booster.marketing.tool_adapters.descriptors import github_issue_descriptor
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from ads_booster.contracts.agent_run import ToolInvocation
    from ads_booster.contracts.tool_capability import ToolDescriptor

REPOSITORY = "corca-ai/ads-booster"
API = f"https://api.github.com/repos/{REPOSITORY}"
CAPABILITY = "github.issue.create"
MAX_TOKEN_BYTES = 4096
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class IssueInput(ContractModel):
    repository: Literal["corca-ai/ads-booster"]
    title: Annotated[str, Field(min_length=1, max_length=256, pattern=r"\S")]
    body: Annotated[str, Field(min_length=1, max_length=20000, pattern=r"\S")]


class Response(Protocol):
    status: int

    def read(self) -> bytes: ...
    def close(self) -> None: ...


class NoRedirect(HTTPRedirectHandler):
    @override
    def redirect_request(
        self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


def open_github(request: Request, *, timeout: float) -> Response:
    # Never forward this credential to a redirected endpoint, including renamed repositories.
    return cast("Response", build_opener(NoRedirect()).open(request, timeout=timeout))


def validate_token(token: str) -> str:
    if (
        not token
        or len(token) > MAX_TOKEN_BYTES
        or not token.isascii()
        or any(c.isspace() for c in token)
    ):
        raise ValueError("github_token_invalid")
    if re.fullmatch(r"[A-Za-z0-9_]+", token) is None:
        raise ValueError("github_token_invalid")
    return token


def token_from_env(env: Mapping[str, str]) -> str | None:
    configured = env.get("TRACE_MARKETING_GITHUB_TOKEN_FILE")
    path = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".config/trace-marketing/github.token"
    )
    if not path.exists() and configured is None:
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("github_token_file_requires_private_regular_file")
    if path.stat().st_size > MAX_TOKEN_BYTES:
        raise ValueError("github_token_invalid")
    return validate_token(path.read_text().strip())


def descriptor(*, now: datetime) -> ToolDescriptor:
    template = github_issue_descriptor(
        installation_id="configured:github", observed_at=now, ready=True
    )
    schema = _JSON.validate_python(IssueInput.model_json_schema())
    return template.model_copy(
        update={"input_schema": schema, "input_schema_sha256": contract_sha256(schema)}
    )


class GitHubRejectedError(ValueError):
    def __init__(self, code: int) -> None:
        super().__init__(f"github_http_{code}")
        self.code: int = code


@dataclass(slots=True)
class GitHubIssues:
    token: str = field(repr=False)
    opener: Callable[..., Response] = open_github

    def _request(self, method: str, suffix: str, payload: JsonObject | None = None) -> JsonObject:
        request = Request(  # noqa: S310 - fixed GitHub HTTPS repository; no redirects.
            API + suffix,
            data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
            headers={
                "Authorization": f"Bearer {validate_token(self.token)}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": "trace-marketing",
            },
            method=method,
        )
        try:
            response = self.opener(request, timeout=30)
            try:
                expected = 201 if method == "POST" else 200
                if response.status != expected:
                    raise ValueError("github_unexpected_status")
                return _JSON.validate_json(response.read())
            finally:
                response.close()
        except HTTPError as error:
            code = error.code
            error.close()
            raise GitHubRejectedError(code) from None
        except Exception:  # noqa: BLE001 - sanitize provider errors, never retry mutations.
            # Never expose URLs from redirects, provider bodies or credentials in errors.
            raise ValueError("github_response_unconfirmed") from None

    def check_access(self) -> None:
        result = self._request("GET", "")
        if (
            result.get("full_name") != REPOSITORY
            or result.get("has_issues") is not True
            or result.get("archived") is not False
        ):
            raise ValueError("github_repository_unavailable")

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        _ = descriptor
        issue = IssueInput.model_validate(invocation.input)
        payload: JsonObject = {"title": issue.title, "body": issue.body}
        try:
            created = self._request("POST", "/issues", payload)
        except GitHubRejectedError as error:
            if error.code not in {400, 401, 403, 404, 410, 422, 429}:
                raise
            return DelegatedToolResult(
                disposition="failed",
                output={"repository": REPOSITORY, "error": str(error)},
                actual_cost_units=1,
            )
        number = created.get("number")
        if type(number) is not int or number < 1:
            raise ValueError("github_issue_response_invalid")
        url = f"https://github.com/{REPOSITORY}/issues/{number}"
        if created.get("html_url") != url or "pull_request" in created:
            raise ValueError("github_issue_response_invalid")
        # Read back the exact immutable approved text; never trust an invented model URL.
        observed = self._request("GET", f"/issues/{number}")
        if (
            any(
                observed.get(key) != value
                for key, value in {**payload, "number": number, "html_url": url}.items()
            )
            or "pull_request" in observed
        ):
            raise ValueError("github_issue_readback_mismatch")
        return DelegatedToolResult(
            disposition="succeeded",
            output={"repository": REPOSITORY, "number": number, "url": url},
            actual_cost_units=1,
        )
