from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from ads_booster.transport.json_types import JsonObject


class ToolDescriptor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    type: Literal["function"] = "function"
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parameters: JsonObject
    strict: bool = False
