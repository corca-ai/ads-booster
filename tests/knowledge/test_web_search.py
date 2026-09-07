from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from ads_booster.knowledge.web_search import (
    DdgsSearchProvider,
    ExternalSearchPolicy,
    ProviderSearchCandidate,
    ProviderSearchError,
    ProviderSearchRequest,
    SearchBudget,
    SearchConfiguration,
    SourceSearch,
    SourceSearchRequest,
    SourceSearchStatus,
)


@dataclass(slots=True)
class ControlledSearchProvider:
    name: str = "controlled"
    candidates: tuple[ProviderSearchCandidate, ...] = ()
    failure: ProviderSearchError | None = None
    calls: list[ProviderSearchRequest] = field(default_factory=list)

    def search(self, request: ProviderSearchRequest) -> tuple[ProviderSearchCandidate, ...]:
        self.calls.append(request)
        if self.failure is not None:
            raise self.failure
        return self.candidates


@dataclass(slots=True)
class ControlledDdgsDriver:
    rows: list[dict[str, str]]
    calls: list[tuple[str, int, str | None]] = field(default_factory=list)

    def text(
        self,
        query: str,
        *,
        max_results: int,
        region: str | None = None,
    ) -> list[dict[str, str]]:
        self.calls.append((query, max_results, region))
        return self.rows


def _budget(*, remaining_calls: int = 1) -> SearchBudget:
    return SearchBudget(
        remaining_calls=remaining_calls,
        deadline=datetime(2030, 1, 1, tzinfo=UTC),
    )


def _search(provider: ControlledSearchProvider) -> SourceSearch:
    return SourceSearch(
        provider=provider,
        configuration=SearchConfiguration(locale="ko-kr", domains=("example.test",)),
    )


def test_source_search_returns_bounded_discovery_from_configured_scope() -> None:
    # Given
    provider = ControlledSearchProvider(
        candidates=tuple(
            ProviderSearchCandidate(
                url=f"https://www.example.test/article-{number}",
                title=f"Article {number}",
                snippet=f"Discovery {number}",
            )
            for number in range(7)
        )
    )

    # When
    result = _search(provider).search(
        SourceSearchRequest(query="public market trend", limit=5),
        policy=ExternalSearchPolicy(public_search_allowed=True),
        budget=_budget(),
    )

    # Then
    assert result.status is SourceSearchStatus.RESULTS
    assert result.discovery is not None
    assert result.discovery.provider == "controlled"
    assert result.discovery.fetched_at.tzinfo is UTC
    assert [item.url for item in result.discovery.candidates] == [
        f"https://www.example.test/article-{number}" for number in range(5)
    ]
    assert provider.calls == [
        ProviderSearchRequest(
            query="public market trend",
            locale="ko-kr",
            domains=("example.test",),
            limit=5,
        )
    ]


def test_source_search_returns_no_results_without_fabricating_a_discovery() -> None:
    # Given
    provider = ControlledSearchProvider()

    # When
    result = _search(provider).search(
        SourceSearchRequest(query="public market trend"),
        policy=ExternalSearchPolicy(public_search_allowed=True),
        budget=_budget(),
    )

    # Then
    assert result.status is SourceSearchStatus.NO_RESULTS
    assert result.discovery is None
    assert result.error_code is None


def test_source_search_stops_before_provider_when_budget_is_exhausted() -> None:
    # Given
    provider = ControlledSearchProvider()

    # When
    result = _search(provider).search(
        SourceSearchRequest(query="public market trend"),
        policy=ExternalSearchPolicy(public_search_allowed=True),
        budget=_budget(remaining_calls=0),
    )

    # Then
    assert result.status is SourceSearchStatus.BUDGET_EXHAUSTED
    assert result.error_code == "search_call_budget_exhausted"
    assert provider.calls == []


def test_source_search_stops_before_provider_after_budget_deadline() -> None:
    # Given
    provider = ControlledSearchProvider()
    expired_budget = SearchBudget(
        remaining_calls=1,
        deadline=datetime(2020, 1, 1, tzinfo=UTC),
    )

    # When
    result = _search(provider).search(
        SourceSearchRequest(query="public market trend"),
        policy=ExternalSearchPolicy(public_search_allowed=True),
        budget=expired_budget,
    )

    # Then
    assert result.status is SourceSearchStatus.BUDGET_EXHAUSTED
    assert provider.calls == []


def test_source_search_reports_provider_timeout_as_unavailable() -> None:
    # Given
    provider = ControlledSearchProvider(failure=ProviderSearchError("timeout", retryable=True))

    # When
    result = _search(provider).search(
        SourceSearchRequest(query="public market trend"),
        policy=ExternalSearchPolicy(public_search_allowed=True),
        budget=_budget(),
    )

    # Then
    assert result.status is SourceSearchStatus.SEARCH_UNAVAILABLE
    assert result.error_code == "provider_timeout"
    assert result.retryable is True


def test_source_search_drops_malformed_or_unconfigured_urls() -> None:
    # Given
    provider = ControlledSearchProvider(
        candidates=(
            ProviderSearchCandidate(
                url="https://outside.test/article",
                title="Outside scope",
                snippet="not selected",
            ),
            ProviderSearchCandidate(
                url="file:///private",
                title="Malformed",
                snippet="not selected",
            ),
        )
    )

    # When
    result = _search(provider).search(
        SourceSearchRequest(query="public market trend"),
        policy=ExternalSearchPolicy(public_search_allowed=True),
        budget=_budget(),
    )

    # Then
    assert result.status is SourceSearchStatus.NO_RESULTS
    assert result.discovery is None


def test_source_search_does_not_transmit_when_policy_forbids_public_search() -> None:
    # Given
    provider = ControlledSearchProvider()

    # When
    result = _search(provider).search(
        SourceSearchRequest(query="public-only query"),
        policy=ExternalSearchPolicy(public_search_allowed=False),
        budget=_budget(),
    )

    # Then
    assert result.status is SourceSearchStatus.SEARCH_UNAVAILABLE
    assert result.error_code == "external_transmission_forbidden"
    assert provider.calls == []


def test_ddgs_adapter_uses_controlled_raw_public_rows_without_network() -> None:
    # Given
    driver = ControlledDdgsDriver(
        rows=[
            {
                "href": "https://www.example.test/report",
                "title": "Public report",
                "body": "Untrusted discovery snippet",
            }
        ]
    )
    provider = DdgsSearchProvider(driver=driver)

    # When
    candidates = provider.search(
        ProviderSearchRequest(
            query="public market trend",
            locale="ko-kr",
            domains=("example.test",),
            limit=1,
        )
    )

    # Then
    assert candidates == (
        ProviderSearchCandidate(
            url="https://www.example.test/report",
            title="Public report",
            snippet="Untrusted discovery snippet",
        ),
    )
    assert driver.calls == [("public market trend site:example.test", 1, "ko-kr")]
