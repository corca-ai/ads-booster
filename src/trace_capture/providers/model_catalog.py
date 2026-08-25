from __future__ import annotations

from typing import TYPE_CHECKING, Final

import httpx2
from pydantic import ValidationError

from trace_capture.auth.codex import CODEX_BACKEND_URL, CodexOAuth, OAuthError
from trace_capture.providers.models import (
    ProviderModel,
    ProviderModelCatalog,
    ProviderReasoningLevel,
)

if TYPE_CHECKING:
    from trace_capture.auth.models import OAuthCredential
    from trace_capture.transport.http import HttpClient

HTTP_OK: Final = 200
CLIENT_VERSION: Final = "0.149.0"

DEFAULT_OPENAI_MODELS: Final[tuple[ProviderModel, ...]] = (
    ProviderModel(
        slug="gpt-5.5",
        display_name="GPT-5.5",
        description="Frontier reasoning and coding model",
        default_reasoning_level="medium",
        supported_reasoning_levels=(
            ProviderReasoningLevel(effort="low", description="Fast reasoning"),
            ProviderReasoningLevel(effort="medium", description="Balanced reasoning"),
            ProviderReasoningLevel(effort="high", description="Deep reasoning"),
            ProviderReasoningLevel(effort="xhigh", description="Maximum reasoning"),
        ),
    ),
    ProviderModel(
        slug="gpt-5.4",
        display_name="GPT-5.4",
        description="Fast and capable balanced model",
        default_reasoning_level="medium",
        supported_reasoning_levels=(
            ProviderReasoningLevel(effort="low", description="Fast reasoning"),
            ProviderReasoningLevel(effort="medium", description="Balanced reasoning"),
            ProviderReasoningLevel(effort="high", description="Deep reasoning"),
        ),
    ),
    ProviderModel(
        slug="gpt-5",
        display_name="GPT-5",
        description="Core multimodal model",
    ),
    ProviderModel(
        slug="gpt-5-mini",
        display_name="GPT-5 mini",
        description="Lightweight and fast",
    ),
    ProviderModel(
        slug="o3",
        display_name="o3",
        description="Specialized high-reasoning model",
        default_reasoning_level="medium",
        supported_reasoning_levels=(
            ProviderReasoningLevel(effort="low", description="Fast reasoning"),
            ProviderReasoningLevel(effort="medium", description="Balanced reasoning"),
            ProviderReasoningLevel(effort="high", description="Deep reasoning"),
        ),
    ),
    ProviderModel(
        slug="o3-mini",
        display_name="o3-mini",
        description="Lightweight reasoning model",
        default_reasoning_level="medium",
        supported_reasoning_levels=(
            ProviderReasoningLevel(effort="low", description="Fast reasoning"),
            ProviderReasoningLevel(effort="medium", description="Balanced reasoning"),
            ProviderReasoningLevel(effort="high", description="Deep reasoning"),
        ),
    ),
)


def available_models(http: HttpClient, oauth: CodexOAuth) -> tuple[ProviderModel, ...]:
    try:
        credential = oauth.refresh_if_needed()
        response = http.get(
            f"{CODEX_BACKEND_URL}/models?client_version={CLIENT_VERSION}",
            _request_headers(credential),
        )
    except OAuthError:
        raise
    except httpx2.HTTPError:
        return DEFAULT_OPENAI_MODELS
    if response.status_code != HTTP_OK:
        return DEFAULT_OPENAI_MODELS
    try:
        catalog = ProviderModelCatalog.model_validate(response.json_object())
    except ValidationError:
        return DEFAULT_OPENAI_MODELS
    models = tuple(
        model for model in catalog.models if model.visibility == "list" and model.supported_in_api
    )
    if not models:
        return DEFAULT_OPENAI_MODELS
    return models


def _request_headers(credential: OAuthCredential) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"{credential.token_type} {credential.access_token}",
        "User-Agent": "trace-agent/0.1.0",
    }
    if credential.account_id:
        headers["chatgpt-account-id"] = credential.account_id
    return headers
