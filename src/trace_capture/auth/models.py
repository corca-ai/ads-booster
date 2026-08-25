from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class OAuthCredential(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    provider: Literal["openai-codex"] = "openai-codex"
    access_token: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    expires_at: float = Field(gt=0)
    account_id: str | None = None
    token_type: str = Field(default="Bearer", min_length=1)


class DeviceCodePayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    device_auth_id: str = Field(min_length=1)
    user_code: str = Field(min_length=1)
    interval: int = Field(default=5, ge=1, le=60)


class DeviceTokenPayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    authorization_code: str = Field(min_length=1)
    code_verifier: str = Field(min_length=1)


class OAuthTokenPayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    access_token: str = Field(min_length=1)
    refresh_token: str | None = Field(default=None, min_length=1)
    expires_in: int = Field(default=3600, ge=60, le=86_400 * 30)
    token_type: str = Field(default="Bearer", min_length=1)
