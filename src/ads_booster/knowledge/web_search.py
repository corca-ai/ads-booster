"""Bounded public-web discovery that never treats snippets as source evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Annotated, Final, Protocol, final, override
from urllib.parse import urlsplit
from uuid import uuid4

from ddgs.exceptions import DDGSException, TimeoutException
from pydantic import (
    Field,
    ValidationError,
)

from ads_booster.contracts.agent_run import BoundedId  # noqa: TC001
from ads_booster.contracts.models import ContractModel, Locale
from ads_booster.knowledge.contract_types import UtcDatetime  # noqa: TC001
from ads_booster.marketing.agent_service.web_search import public_search

if TYPE_CHECKING:
    from collections.abc import Sequence

MAX_SOURCE_SEARCH_RESULTS: Final = 5
_MAX_DOMAINS: Final = 5
_MAX_QUERY_LENGTH: Final = 500
_MAX_TITLE_LENGTH: Final = 300
_MAX_SNIPPET_LENGTH: Final = 2_000
_MAX_URL_LENGTH: Final = 2_048
_PROVIDER_TIMEOUT: Final = "timeout"
_PROVIDER_UNAVAILABLE: Final = "unavailable"
_PROVIDER_INVALID_RESPONSE: Final = "invalid_response"
SearchDomain = Annotated[
    str,
    Field(
        min_length=1,
        max_length=253,
        pattern=r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$",
    ),
]
SearchText = Annotated[str, Field(min_length=1, max_length=_MAX_QUERY_LENGTH)]


class SourceSearchRequest(ContractModel):
    query: SearchText
    locale: Locale | None = None
    domains: Annotated[tuple[SearchDomain, ...], Field(max_length=_MAX_DOMAINS)] = ()
    limit: Annotated[int, Field(ge=1, le=MAX_SOURCE_SEARCH_RESULTS)] = MAX_SOURCE_SEARCH_RESULTS


class SearchConfiguration(ContractModel):
    locale: Locale | None = None
    domains: Annotated[tuple[SearchDomain, ...], Field(max_length=_MAX_DOMAINS)] = ()


class ExternalSearchPolicy(ContractModel):
    public_search_allowed: bool


class SearchBudget(ContractModel):
    remaining_calls: Annotated[int, Field(ge=0, le=10_000)]
    deadline: UtcDatetime


@dataclass(frozen=True, slots=True)
class ProviderSearchRequest:
    query: str
    locale: str | None
    domains: tuple[str, ...]
    limit: int


@dataclass(frozen=True, slots=True)
class ProviderSearchCandidate:
    url: str
    title: str
    snippet: str


@dataclass(slots=True)
class ProviderSearchError(Exception):
    code: str
    retryable: bool

    @override
    def __str__(self) -> str:
        return self.code


class SearchProvider(Protocol):
    name: str

    def search(self, request: ProviderSearchRequest) -> tuple[ProviderSearchCandidate, ...]: ...


class DdgsTextDriver(Protocol):
    def text(
        self,
        query: str,
        *,
        max_results: int,
        region: str | None = None,
    ) -> list[dict[str, str]]: ...


@final
class _LiveDdgsDriver:
    def text(
        self,
        query: str,
        *,
        max_results: int,
        region: str | None = None,
    ) -> list[dict[str, str]]:
        _ = region
        return public_search(query)[:max_results]


@final
class DdgsSearchProvider:
    """The configured free DDGS adapter; it never activates a paid provider."""

    name: Final[str] = "ddgs"

    def __init__(
        self,
        *,
        driver: DdgsTextDriver | None = None,
    ) -> None:
        """Use a controlled driver or the installed DDGS adapter."""
        self._driver = driver or _LiveDdgsDriver()

    def search(self, request: ProviderSearchRequest) -> tuple[ProviderSearchCandidate, ...]:
        try:
            rows = self._driver.text(
                _query_with_configured_domains(request),
                max_results=request.limit,
                region=request.locale,
            )
        except TimeoutException as error:
            raise ProviderSearchError(_PROVIDER_TIMEOUT, retryable=True) from error
        except DDGSException as error:
            raise ProviderSearchError(_PROVIDER_UNAVAILABLE, retryable=True) from error
        except OSError as error:
            raise ProviderSearchError(_PROVIDER_UNAVAILABLE, retryable=True) from error
        except ValidationError as error:
            raise ProviderSearchError(_PROVIDER_INVALID_RESPONSE, retryable=False) from error
        return tuple(
            ProviderSearchCandidate(
                url=row.get("href", ""),
                title=row.get("title", ""),
                snippet=row.get("body", ""),
            )
            for row in rows
        )


@unique
class SourceSearchStatus(StrEnum):
    RESULTS = "results"
    NO_RESULTS = "no_results"
    SEARCH_UNAVAILABLE = "search_unavailable"
    BUDGET_EXHAUSTED = "budget_exhausted"


class SourceSearchCandidate(ContractModel):
    url: Annotated[str, Field(min_length=1, max_length=_MAX_URL_LENGTH)]
    title: Annotated[str, Field(max_length=_MAX_TITLE_LENGTH)] = ""
    snippet: Annotated[str, Field(max_length=_MAX_SNIPPET_LENGTH)] = ""


class SourceDiscovery(ContractModel):
    discovery_id: BoundedId
    provider: Annotated[str, Field(min_length=1, max_length=64)]
    fetched_at: UtcDatetime
    candidates: Annotated[
        tuple[SourceSearchCandidate, ...], Field(min_length=1, max_length=MAX_SOURCE_SEARCH_RESULTS)
    ]


class SourceSearchResult(ContractModel):
    status: SourceSearchStatus
    discovery: SourceDiscovery | None = None
    error_code: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    retryable: bool = False


@final
class SourceSearch:
    def __init__(self, *, provider: SearchProvider, configuration: SearchConfiguration) -> None:  # noqa: D107
        self._provider = provider
        self._configuration = configuration

    def search(
        self,
        request: SourceSearchRequest,
        *,
        policy: ExternalSearchPolicy,
        budget: SearchBudget,
    ) -> SourceSearchResult:
        now = datetime.now(UTC)
        if not policy.public_search_allowed:
            return _unavailable("external_transmission_forbidden", retryable=False)
        if budget.remaining_calls == 0 or now >= budget.deadline:
            return _budget_exhausted()
        provider_request = _provider_request(request, self._configuration)
        if provider_request is None:
            return _unavailable("search_scope_not_configured", retryable=False)
        try:
            candidates = self._provider.search(provider_request)
        except ProviderSearchError as error:
            return _unavailable(f"provider_{error.code}", retryable=error.retryable)
        discoveries = _bounded_candidates(candidates, provider_request.domains, request.limit)
        if not discoveries:
            return SourceSearchResult(status=SourceSearchStatus.NO_RESULTS)
        return SourceSearchResult(
            status=SourceSearchStatus.RESULTS,
            discovery=SourceDiscovery(
                discovery_id=f"discovery.{uuid4().hex}",
                provider=self._provider.name,
                fetched_at=now,
                candidates=discoveries,
            ),
        )


def _provider_request(
    request: SourceSearchRequest,
    configuration: SearchConfiguration,
) -> ProviderSearchRequest | None:
    if request.locale is not None and request.locale != configuration.locale:
        return None
    configured_domains = tuple(domain.lower() for domain in configuration.domains)
    requested_domains = request.domains or configuration.domains
    normalized_domains = tuple(domain.lower() for domain in requested_domains)
    if any(domain not in configured_domains for domain in normalized_domains):
        return None
    return ProviderSearchRequest(
        query=request.query,
        locale=configuration.locale,
        domains=normalized_domains,
        limit=request.limit,
    )


def _query_with_configured_domains(request: ProviderSearchRequest) -> str:
    if not request.domains:
        return request.query
    return f"{request.query} {' OR '.join(f'site:{domain}' for domain in request.domains)}"


def _bounded_candidates(
    candidates: Sequence[ProviderSearchCandidate],
    configured_domains: tuple[str, ...],
    limit: int,
) -> tuple[SourceSearchCandidate, ...]:
    bounded: list[SourceSearchCandidate] = []
    for candidate in candidates:
        parsed = urlsplit(candidate.url)
        hostname = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or len(candidate.url) > _MAX_URL_LENGTH
            or not _matches_domain(hostname, configured_domains)
        ):
            continue
        bounded.append(
            SourceSearchCandidate(
                url=candidate.url,
                title=candidate.title[:_MAX_TITLE_LENGTH],
                snippet=candidate.snippet[:_MAX_SNIPPET_LENGTH],
            )
        )
        if len(bounded) == limit:
            break
    return tuple(bounded)


def _matches_domain(hostname: str, configured_domains: tuple[str, ...]) -> bool:
    if not configured_domains:
        return True
    normalized_host = hostname.rstrip(".").lower()
    return any(
        normalized_host == domain or normalized_host.endswith(f".{domain}")
        for domain in configured_domains
    )


def _unavailable(error_code: str, *, retryable: bool) -> SourceSearchResult:
    return SourceSearchResult(
        status=SourceSearchStatus.SEARCH_UNAVAILABLE,
        error_code=error_code,
        retryable=retryable,
    )


def _budget_exhausted() -> SourceSearchResult:
    return SourceSearchResult(
        status=SourceSearchStatus.BUDGET_EXHAUSTED,
        error_code="search_call_budget_exhausted",
    )
