from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ads_booster.contracts.tools import ToolDescriptor
from ads_booster.transport.json_types import JsonObject


class ProviderReasoningLevel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    effort: str = Field(min_length=1)
    description: str = ""


class ProviderModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    slug: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    description: str = ""
    visibility: str = "list"
    supported_in_api: bool = True
    default_reasoning_level: str | None = None
    supported_reasoning_levels: tuple[ProviderReasoningLevel, ...] = ()


class ProviderModelCatalog(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    models: tuple[ProviderModel, ...] = ()


class ResponseContent(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow", frozen=True)

    type: str = Field(min_length=1)
    text: str | None = None


class ProviderInputTokenDetails(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    cached_tokens: int | None = Field(default=None, ge=0)


class ProviderUsage(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    input_tokens_details: ProviderInputTokenDetails | None = None


class ProviderCacheMetrics(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    prefix_digest: str = Field(min_length=1)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cache_hit: bool | None = None


class ProviderResponseMetadata(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    response_id: str | None = None
    usage: ProviderUsage | None = None
    cache: ProviderCacheMetrics


class ResponseOutputItem(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow", frozen=True)

    type: str = Field(min_length=1)
    call_id: str | None = None
    name: str | None = None
    arguments: str | None = None
    content: tuple[ResponseContent, ...] = ()


class ResponseEnvelope(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow", frozen=True)

    id: str | None = None
    output: tuple[ResponseOutputItem, ...] = ()
    usage: ProviderUsage | None = None
    incomplete_details: JsonObject | None = None


class ReasoningSettings(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    effort: str = Field(min_length=1)


class ResponsesRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    model: str = Field(min_length=1)
    input: tuple[JsonObject, ...]
    instructions: str = Field(min_length=1)
    tools: tuple[ToolDescriptor, ...] = ()
    reasoning: ReasoningSettings | None = None
    stream: bool = True
    store: bool = False
    parallel_tool_calls: bool = False
