from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, override

from ads_booster.agent.run_models import (
    AgentGoal,
    AgentRun,
    AgentRunModel,
    CompletionDecision,
    ConnectorId,
)

if TYPE_CHECKING:
    from ads_booster.tools.models import Tool
    from ads_booster.transport.json_types import JsonObject


class ConnectorManifest(AgentRunModel):
    connector_id: ConnectorId
    version: str
    description: str


class DomainConnector(Protocol):
    @property
    def manifest(self) -> ConnectorManifest: ...

    def instructions(self, goal: AgentGoal) -> str: ...

    def context_messages(self, goal: AgentGoal) -> tuple[JsonObject, ...]: ...

    def tools(self, goal: AgentGoal) -> tuple[Tool, ...]: ...

    def validate_completion(self, run: AgentRun, answer: str) -> CompletionDecision: ...


class ConnectorContextError(RuntimeError):
    pass


class ConnectorAlreadyRegisteredError(RuntimeError):
    connector_id: ConnectorId
    version: str

    def __init__(self, connector_id: ConnectorId, version: str) -> None:
        """Create a duplicate-connector registration failure."""
        self.connector_id = connector_id
        self.version = version
        super().__init__(connector_id, version)

    @override
    def __str__(self) -> str:
        """Render the duplicate connector identity."""
        return f"connector already registered: {self.connector_id}@{self.version}"


class ConnectorNotFoundError(RuntimeError):
    connector_id: ConnectorId
    version: str

    def __init__(self, connector_id: ConnectorId, version: str) -> None:
        """Create a missing-connector lookup failure."""
        self.connector_id = connector_id
        self.version = version
        super().__init__(connector_id, version)

    @override
    def __str__(self) -> str:
        """Render the missing connector identity."""
        return f"connector not found: {self.connector_id}@{self.version}"


@dataclass(frozen=True, slots=True)
class ConnectorRegistry:
    connectors: tuple[DomainConnector, ...]

    def __post_init__(self) -> None:
        """Reject ambiguous connector identities before the registry is used."""
        seen: set[tuple[ConnectorId, str]] = set()
        for connector in self.connectors:
            manifest = connector.manifest
            identity = (manifest.connector_id, manifest.version)
            if identity in seen:
                raise ConnectorAlreadyRegisteredError(*identity)
            seen.add(identity)

    def get(self, connector_id: ConnectorId, version: str) -> DomainConnector:
        for connector in self.connectors:
            manifest = connector.manifest
            if manifest.connector_id == connector_id and manifest.version == version:
                return connector
        raise ConnectorNotFoundError(connector_id, version)

    def ids(self) -> tuple[ConnectorId, ...]:
        return tuple(connector.manifest.connector_id for connector in self.connectors)
