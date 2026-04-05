"""OpenAI provider client.

Reasoning: uses reasoning_effort top-level string field in Chat Completions API.
This is the correct format per official docs (GPT-5.2 Guide).
The Responses API uses a different reasoning object format — not used here.
See docs/dev/provider-api-spec.md.
"""

from typing import Any

from ...core.schema import OpenAIConfig, OpenAIReasoningConfig
from .openai_compat import OpenAICompatibleClient


def _map_reasoning_effort(
    reasoning: OpenAIReasoningConfig | None,
    provider_overrides: dict[str, Any] | None,
) -> str | None:
    """Map reasoning config to Chat Completions reasoning_effort string."""
    if provider_overrides:
        override = provider_overrides.get("openai_reasoning_effort")
        if override is not None:
            if not isinstance(override, str) or not override.strip():
                raise ValueError(
                    "provider_overrides.openai_reasoning_effort must be a string"
                )
            return override

    if reasoning is None:
        return None
    if reasoning.effort is not None:
        return reasoning.effort
    return None


class OpenAIClient(OpenAICompatibleClient):
    def __init__(self, config: OpenAIConfig, *, prompt_cache_retention: str | None = None):
        self.api_key = config.api_key
        # GPT-5+ requires max_completion_tokens instead of max_tokens
        use_mct = config.use_max_completion_tokens
        super().__init__(
            model=config.model,
            base_url=config.base_url,
            max_tokens=None if use_mct else config.max_tokens,
            max_completion_tokens=config.max_tokens if use_mct else None,
            request_timeout=config.request_timeout,
            reasoning_effort=_map_reasoning_effort(
                config.reasoning,
                config.provider_overrides,
            ),
            temperature=config.temperature,
            prompt_cache_retention=prompt_cache_retention,
        )

    def _get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
