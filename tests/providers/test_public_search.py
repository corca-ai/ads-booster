from __future__ import annotations

from dataclasses import dataclass

import pytest
from ddgs.exceptions import TimeoutException
from pydantic import ValidationError

import ads_booster.providers.public_search as public_search_module


@dataclass
class ControlledDdgs:
    response: object
    calls: list[tuple[str, int]]
    error: Exception | None = None

    def text(self, query: str, *, max_results: int) -> object:
        self.calls.append((query, max_results))
        if self.error is not None:
            raise self.error
        return self.response


def _replace_ddgs(
    monkeypatch: pytest.MonkeyPatch, *, response: object, error: Exception | None = None
) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []

    def _factory(*, timeout: int) -> ControlledDdgs:
        assert timeout == 15
        return ControlledDdgs(response=response, calls=calls, error=error)

    monkeypatch.setattr(public_search_module, "DDGS", _factory)
    return calls


def test_public_search_preserves_ddgs_timeout_result_bound_and_row_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _replace_ddgs(
        monkeypatch,
        response=[{"href": "https://example.test", "title": "Example", "body": "Snippet"}],
    )

    rows = public_search_module.public_search("public market trend")

    assert rows == [{"href": "https://example.test", "title": "Example", "body": "Snippet"}]
    assert calls == [("public market trend", 5)]


def test_public_search_preserves_invalid_provider_response_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = _replace_ddgs(monkeypatch, response={"href": "https://example.test"})

    with pytest.raises(ValidationError):
        _ = public_search_module.public_search("public market trend")


def test_public_search_preserves_provider_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = TimeoutException("fixture timeout")
    _ = _replace_ddgs(monkeypatch, response=[], error=error)

    with pytest.raises(TimeoutException) as raised:
        _ = public_search_module.public_search("public market trend")

    assert raised.value is error
