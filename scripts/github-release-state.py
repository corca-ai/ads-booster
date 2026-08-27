# ruff: noqa: INP001
"""Classify and repair workflow-owned GitHub release state."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast, final
from urllib.parse import quote

if TYPE_CHECKING:
    from http.client import HTTPResponse

_COMMIT_SHA: Final = re.compile(r"[0-9a-f]{40}")
_NOT_FOUND: Final = 404
_REPOSITORY: Final = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SEMVER: Final = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_RETRYABLE_STATUS: Final = frozenset({429, 500, 502, 503, 504})


class ReleaseStateError(RuntimeError):
    """The remote state is ambiguous, conflicting, or unavailable."""


@final
class GitHubApi:
    """Small authenticated REST client with strict absence semantics."""

    def __init__(self, *, base_url: str, token: str, attempts: int = 5) -> None:
        """Create a client for one HTTPS GitHub API origin."""
        if not base_url.startswith("https://"):
            message = "GitHub API URL must use HTTPS"
            raise ValueError(message)
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._attempts = attempts

    def get_optional(self, path: str) -> dict[str, object] | None:
        return self._request("GET", path, optional_404=True)

    def delete(self, path: str) -> None:
        _ = self._request("DELETE", path, optional_404=True)

    def _request(
        self,
        method: str,
        path: str,
        *,
        optional_404: bool,
    ) -> dict[str, object] | None:
        url = f"{self._base_url}/{path.lstrip('/')}"
        request = urllib.request.Request(  # noqa: S310 -- HTTPS is validated in __init__.
            url,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": "trace-marketing-release-state",
            },
        )
        for attempt in range(1, self._attempts + 1):
            try:
                with cast(
                    "HTTPResponse",
                    urllib.request.urlopen(request, timeout=30),  # noqa: S310
                ) as response:
                    payload: bytes = response.read()
                if not payload:
                    return None
                decoded = cast("object", json.loads(payload))
                if not isinstance(decoded, dict):
                    message = "GitHub API returned a non-object response"
                    raise ReleaseStateError(message)
                return cast("dict[str, object]", decoded)
            except urllib.error.HTTPError as error:
                error.close()
                if error.code == _NOT_FOUND and optional_404:
                    return None
                if error.code not in _RETRYABLE_STATUS or attempt == self._attempts:
                    message = f"GitHub API returned HTTP {error.code}"
                    raise ReleaseStateError(message) from None
            except (TimeoutError, urllib.error.URLError) as error:
                if attempt == self._attempts:
                    message = "GitHub API transport failed after retries"
                    raise ReleaseStateError(message) from error
            time.sleep(2)
        message = "unreachable GitHub API retry state"
        raise AssertionError(message)


def main() -> None:
    arguments = _arguments()
    repository = cast("str", arguments.repository)
    version = cast("str", arguments.version)
    commit_sha = cast("str", arguments.commit_sha)
    if _REPOSITORY.fullmatch(repository) is None:
        message = "repository must use owner/name"
        raise SystemExit(message)
    if _SEMVER.fullmatch(version) is None:
        message = "version must use strict semantic versioning"
        raise SystemExit(message)
    if _COMMIT_SHA.fullmatch(commit_sha) is None:
        message = "commit SHA must contain exactly 40 lower-case hex characters"
        raise SystemExit(message)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        message = "GITHUB_TOKEN is required"
        raise SystemExit(message)
    api = GitHubApi(
        base_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        token=token,
    )
    state = resolve_release_state(
        api,
        repository=repository,
        version=version,
        commit_sha=commit_sha,
        repair_managed_partials=cast("bool", arguments.repair_managed_partials),
    )
    if cast("bool", arguments.forbid_existing) and state != "new":
        message = f"v{version} already has GitHub release state {state}"
        raise SystemExit(message)
    output = cast("Path | None", arguments.github_output)
    if output is not None:
        with output.open("a", encoding="utf-8") as stream:
            _ = stream.write(f"state={state}\n")
    _ = sys.stdout.write(f"{state}\n")


def resolve_release_state(
    api: GitHubApi,
    *,
    repository: str,
    version: str,
    commit_sha: str,
    repair_managed_partials: bool,
) -> str:
    tag = f"v{version}"
    encoded_tag = quote(tag, safe="")
    release = api.get_optional(f"repos/{repository}/releases/tags/{encoded_tag}")
    ref = api.get_optional(f"repos/{repository}/git/ref/tags/{encoded_tag}")
    tag_object = _tag_object(api, repository=repository, ref=ref)
    state = classify_release_state(
        release=release,
        ref=ref,
        tag_object=tag_object,
        tag=tag,
        commit_sha=commit_sha,
    )
    if state != "repair" or not repair_managed_partials:
        return state
    if release is not None:
        release_id = release.get("id")
        if not isinstance(release_id, int):
            message = "managed partial release has no numeric database ID"
            raise ReleaseStateError(message)
        api.delete(f"repos/{repository}/releases/{release_id}")
    if ref is not None:
        api.delete(f"repos/{repository}/git/refs/tags/{encoded_tag}")
    remaining_release = api.get_optional(f"repos/{repository}/releases/tags/{encoded_tag}")
    remaining_ref = api.get_optional(f"repos/{repository}/git/ref/tags/{encoded_tag}")
    if remaining_release is not None or remaining_ref is not None:
        message = "managed partial release repair did not converge to empty state"
        raise ReleaseStateError(message)
    return "new"


def classify_release_state(
    *,
    release: dict[str, object] | None,
    ref: dict[str, object] | None,
    tag_object: dict[str, object] | None,
    tag: str,
    commit_sha: str,
) -> str:
    if release is None and ref is None:
        return "new"
    if (
        release is not None
        and ref is not None
        and _is_exact_immutable_release(release, tag=tag, commit_sha=commit_sha)
        and _is_managed_tag(
            ref,
            tag_object=tag_object,
            tag=tag,
            commit_sha=commit_sha,
        )
    ):
        return "resume"
    release_owned = release is None or _is_managed_partial_release(
        release,
        tag=tag,
        commit_sha=commit_sha,
    )
    tag_owned = ref is None or _is_managed_tag(
        ref,
        tag_object=tag_object,
        tag=tag,
        commit_sha=commit_sha,
    )
    if release_owned and tag_owned:
        return "repair"
    message = f"{tag} has conflicting or unowned GitHub release state"
    raise ReleaseStateError(message)


def managed_release_body(tag: str, commit_sha: str) -> str:
    return f"<!-- trace-marketing-managed-release:{tag}:{commit_sha} -->"


def managed_tag_message(tag: str, commit_sha: str) -> str:
    return f"Managed trace-marketing release {tag} at {commit_sha}"


def _tag_object(
    api: GitHubApi,
    *,
    repository: str,
    ref: dict[str, object] | None,
) -> dict[str, object] | None:
    if ref is None:
        return None
    raw_ref_object = ref.get("object")
    if not isinstance(raw_ref_object, dict):
        return None
    ref_object = cast("dict[str, object]", raw_ref_object)
    if ref_object.get("type") != "tag":
        return None
    object_sha = ref_object.get("sha")
    if not isinstance(object_sha, str) or _COMMIT_SHA.fullmatch(object_sha) is None:
        return None
    return api.get_optional(f"repos/{repository}/git/tags/{object_sha}")


def _is_exact_immutable_release(
    release: dict[str, object],
    *,
    tag: str,
    commit_sha: str,
) -> bool:
    return (
        release.get("tag_name") == tag
        and release.get("target_commitish") == commit_sha
        and release.get("draft") is False
        and release.get("prerelease") is False
        and release.get("immutable") is True
        and managed_release_body(tag, commit_sha) in str(release.get("body", ""))
    )


def _is_managed_partial_release(
    release: dict[str, object],
    *,
    tag: str,
    commit_sha: str,
) -> bool:
    mutable_or_draft = release.get("draft") is True or release.get("immutable") is False
    return (
        release.get("tag_name") == tag
        and release.get("target_commitish") == commit_sha
        and mutable_or_draft
        and managed_release_body(tag, commit_sha) in str(release.get("body", ""))
    )


def _is_managed_tag(
    ref: dict[str, object],
    *,
    tag_object: dict[str, object] | None,
    tag: str,
    commit_sha: str,
) -> bool:
    raw_ref_object = ref.get("object")
    if not isinstance(raw_ref_object, dict):
        return False
    ref_object = cast("dict[str, object]", raw_ref_object)
    raw_target = tag_object.get("object") if tag_object is not None else None
    if not isinstance(raw_target, dict):
        return False
    target = cast("dict[str, object]", raw_target)
    ref_sha = ref_object.get("sha")
    return (
        ref_object.get("type") == "tag"
        and isinstance(ref_sha, str)
        and tag_object is not None
        and tag_object.get("message") == managed_tag_message(tag, commit_sha)
        and target.get("type") == "commit"
        and target.get("sha") == commit_sha
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--repository", required=True)
    _ = parser.add_argument("--version", required=True)
    _ = parser.add_argument("--commit-sha", required=True)
    _ = parser.add_argument("--repair-managed-partials", action="store_true")
    _ = parser.add_argument("--forbid-existing", action="store_true")
    _ = parser.add_argument("--github-output", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    main()
