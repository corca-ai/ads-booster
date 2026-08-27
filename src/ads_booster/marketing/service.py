from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_CONFIG_FILENAME: Final = "service.json"
_QUEUE_TOKEN_ENV: Final = "TRACE_MARKETING_QUEUE_TOKEN"  # noqa: S105 - variable name.
_WORKER_TOKEN_ENV: Final = "TRACE_MARKETING_WORKER_TOKEN"  # noqa: S105 - variable name.

if TYPE_CHECKING:
    from pathlib import Path


class MarketingBridgeServiceError(RuntimeError):
    pass


@unique
class CredentialProvider(StrEnum):
    ENVIRONMENT = "environment"
    COMMAND = "command"


class MarketingBridgeServiceConfig(BaseModel):
    """Portable, non-secret worker enrollment configuration."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    account_id: str = Field(min_length=1, max_length=128)
    queue_id: str = Field(min_length=1, max_length=256)
    control_plane_url: str = Field(pattern=r"^https://[^\s]+$")
    executor: Literal["simulation"] = "simulation"
    poll_seconds: float = Field(default=2.0, ge=0.1, le=60.0)
    credential_provider: CredentialProvider = CredentialProvider.ENVIRONMENT
    credential_command: tuple[str, ...] = ()


class MarketingBridgeCredentials(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    queue_token: str = Field(min_length=1)
    worker_token: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class MarketingBridgeConfigStore:
    home: Path

    @property
    def path(self) -> Path:
        return self.home / "marketing-bridge" / _CONFIG_FILENAME

    def save(self, config: MarketingBridgeServiceConfig) -> None:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        temporary = self.path.with_suffix(".tmp")
        _ = temporary.write_text(config.model_dump_json(indent=2), encoding="utf-8")
        temporary.chmod(0o600)
        _ = temporary.replace(self.path)

    def load(self) -> MarketingBridgeServiceConfig:
        try:
            return MarketingBridgeServiceConfig.model_validate_json(self.path.read_text())
        except (OSError, ValidationError) as error:
            raise MarketingBridgeServiceError(
                f"marketing bridge config is unavailable: {self.path}"
            ) from error


def resolve_bridge_credentials(
    config: MarketingBridgeServiceConfig,
) -> MarketingBridgeCredentials:
    if config.credential_provider is CredentialProvider.ENVIRONMENT:
        try:
            return MarketingBridgeCredentials(
                queue_token=os.environ[_QUEUE_TOKEN_ENV],
                worker_token=os.environ[_WORKER_TOKEN_ENV],
            )
        except (KeyError, ValidationError) as error:
            raise MarketingBridgeServiceError(
                f"environment provider requires {_QUEUE_TOKEN_ENV} and {_WORKER_TOKEN_ENV}"
            ) from error
    if not config.credential_command:
        raise MarketingBridgeServiceError("command credential provider requires a command")
    try:
        result = subprocess.run(  # noqa: S603
            list(config.credential_command),
            check=False,
            capture_output=True,
            text=True,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except OSError as error:
        raise MarketingBridgeServiceError("credential command could not start") from error
    if result.returncode != 0:
        raise MarketingBridgeServiceError("credential command failed")
    try:
        return MarketingBridgeCredentials.model_validate_json(result.stdout)
    except ValidationError as error:
        raise MarketingBridgeServiceError("credential command returned invalid JSON") from error
