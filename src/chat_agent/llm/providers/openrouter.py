"""OpenRouter provider client.

Reasoning: uses reasoning: {"effort": ...} object format.
Effort vs max_tokens precedence when both set is an adapter rule
(not officially specified by OpenRouter). See docs/dev/provider-api-spec.md.
"""

from typing import Any

from ...core.schema import OpenRouterConfig, OpenRouterReasoningConfig
from .openai_compat import OpenAICompatibleClient


def _map_reasoning(
    reasoning: OpenRouterReasoningConfig | None,
    provider_overrides: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Map reasoning config to OpenRouter reasoning object."""
    if provider_overrides:
        override = provider_overrides.get("openrouter_reasoning")
        if override is not None:
            if not isinstance(override, dict):
                raise ValueError(
                    "provider_overrides.openrouter_reasoning must be an object"
                )
            return override
    if reasoning is None:
        return None
    if reasoning.enabled is False:
        return {"effort": "none"}

    payload: dict[str, Any] = {}
    # Adapter rule: effort takes precedence over max_tokens.
    # OpenRouter docs do not officially specify this precedence.
    if reasoning.effort is not None:
        payload["effort"] = reasoning.effort
    elif reasoning.max_tokens is not None:
        payload["max_tokens"] = reasoning.max_tokens
    return payload or None


class OpenRouterClient(OpenAICompatibleClient):
    def __init__(self, config: OpenRouterConfig):
        self.api_key = config.api_key
        self.site_url = config.site_url
        self.site_name = config.site_name
        super().__init__(
            model=config.model,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            request_timeout=config.request_timeout,
            reasoning_payload=_map_reasoning(
                config.reasoning,
                config.provider_overrides,
            ),
            temperature=config.temperature,
        )

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.site_name:
            headers["X-Title"] = self.site_name
        return headers
