from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


class StructuredReasoningRunner(Protocol):
    def run_marketing_judgment_job(
        self, prompt: str, schema: JsonObject, *, workspace: Path, timeout_seconds: float
    ) -> JsonObject: ...


class ReasoningConfiguration(Protocol):
    @property
    def codex(self) -> StructuredReasoningRunner: ...
    @property
    def workspace_root(self) -> Path: ...
    @property
    def model_id(self) -> str: ...
    @property
    def timeout_seconds(self) -> float: ...
    @property
    def provider_id(self) -> str: ...
