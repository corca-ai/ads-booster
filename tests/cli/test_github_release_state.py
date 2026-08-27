from __future__ import annotations

import importlib.util
import io
import urllib.error
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github-release-state.py"
TAG = "v1.2.3"
SHA = "0123456789abcdef0123456789abcdef01234567"
TAG_OBJECT_SHA = "89abcdef0123456789abcdef0123456789abcdef"


def test_release_state_distinguishes_new_stable_resume_and_owned_draft_repair() -> None:
    module = _module()

    assert (
        module.classify_release_state(
            release=None,
            ref=None,
            tag_object=None,
            tag=TAG,
            commit_sha=SHA,
        )
        == "new"
    )
    assert (
        module.classify_release_state(
            release=_release(module, immutable=False),
            ref=_ref(),
            tag_object=_tag_object(module),
            tag=TAG,
            commit_sha=SHA,
        )
        == "resume"
    )
    assert (
        module.classify_release_state(
            release=_release(module, immutable=True),
            ref=_ref(),
            tag_object=_tag_object(module),
            tag=TAG,
            commit_sha=SHA,
        )
        == "resume"
    )
    assert (
        module.classify_release_state(
            release=_release(module, immutable=False, draft=True),
            ref=None,
            tag_object=None,
            tag=TAG,
            commit_sha=SHA,
        )
        == "repair"
    )
    assert (
        module.classify_release_state(
            release=None,
            ref=_ref(),
            tag_object=_tag_object(module),
            tag=TAG,
            commit_sha=SHA,
        )
        == "repair"
    )


def test_release_state_rejects_unowned_or_conflicting_objects() -> None:
    module = _module()
    external_tag = _tag_object(module)
    external_tag["message"] = "operator tag"

    with pytest.raises(module.ReleaseStateError, match="conflicting or unowned"):
        _ = module.classify_release_state(
            release=_release(module, immutable=False, draft=True),
            ref=_ref(),
            tag_object=external_tag,
            tag=TAG,
            commit_sha=SHA,
        )


def test_repair_removes_only_durably_marked_partial_release_and_tag() -> None:
    module = _module()
    api = _FakeApi(module)

    state = module.resolve_release_state(
        api,
        repository="corca-ai/ads-booster",
        version="1.2.3",
        commit_sha=SHA,
        repair_managed_partials=True,
    )

    assert state == "new"
    assert api.deleted == [
        "repos/corca-ai/ads-booster/releases/123",
        "repos/corca-ai/ads-booster/git/refs/tags/v1.2.3",
    ]
    assert api.release_list_reads >= 2


def test_github_api_treats_only_404_as_absence(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    api = module.GitHubApi(base_url="https://api.github.test", token=SHA, attempts=1)

    def missing(*_args: object, **_kwargs: object) -> object:
        url = "https://api.github.test"
        raise urllib.error.HTTPError(url, 404, "missing", {}, io.BytesIO())

    monkeypatch.setattr(module.urllib.request, "urlopen", missing)
    assert api.get_optional("repos/corca-ai/ads-booster/releases/tags/v1.2.3") is None

    def forbidden(*_args: object, **_kwargs: object) -> object:
        url = "https://api.github.test"
        raise urllib.error.HTTPError(url, 403, "forbidden", {}, io.BytesIO())

    monkeypatch.setattr(module.urllib.request, "urlopen", forbidden)
    with pytest.raises(module.ReleaseStateError, match="HTTP 403"):
        _ = api.get_optional("repos/corca-ai/ads-booster/releases/tags/v1.2.3")


class _FakeApi:
    def __init__(self, module: ModuleType) -> None:
        self.release = _release(module, immutable=False, draft=True)
        self.ref = _ref()
        self.tag_object = _tag_object(module)
        self.deleted: list[str] = []
        self.release_list_reads = 0

    def get_array(self, path: str) -> list[dict[str, object]]:
        assert path.startswith("repos/corca-ai/ads-booster/releases?per_page=100&page=")
        self.release_list_reads += 1
        return [] if self.release is None else [self.release]

    def get_optional(self, path: str) -> dict[str, object] | None:
        if "/git/ref/tags/" in path:
            return self.ref
        if "/git/tags/" in path:
            return self.tag_object
        message = f"unexpected GET {path}"
        raise AssertionError(message)

    def delete(self, path: str) -> None:
        self.deleted.append(path)
        if "/releases/" in path:
            self.release = None
        elif "/git/refs/tags/" in path:
            self.ref = None
            self.tag_object = None
        else:
            message = f"unexpected DELETE {path}"
            raise AssertionError(message)


def _release(
    module: ModuleType,
    *,
    immutable: bool,
    draft: bool = False,
) -> dict[str, object]:
    return {
        "id": 123,
        "tag_name": TAG,
        "target_commitish": SHA,
        "draft": draft,
        "prerelease": False,
        "immutable": immutable,
        "body": module.managed_release_body(TAG, SHA),
    }


def _ref() -> dict[str, object]:
    return {"object": {"type": "tag", "sha": TAG_OBJECT_SHA}}


def _tag_object(module: ModuleType) -> dict[str, object]:
    return {
        "message": module.managed_tag_message(TAG, SHA),
        "object": {"type": "commit", "sha": SHA},
    }


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("github_release_state", SCRIPT)
    if spec is None or spec.loader is None:
        message = "could not load release-state module"
        raise AssertionError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast("ModuleType", module)
