"""Ollama provider client (OpenAI-compatible, local).

Reasoning: sends reasoning_effort via OpenAI-compat endpoint.
NOTE: reasoning_effort is NOT officially documented by Ollama for the
OpenAI-compatible endpoint. This is empirical/compat behavior with no
official guarantee. See docs/dev/provider-api-spec.md.
"""

from typing import Any

from ...core.schema import OllamaConfig, OllamaReasoningConfig
from ..schema import (
    LLMResponse,
    Message,
    OpenAIResponse,
    ToolDefinition,
)
from .openai_compat import OpenAICompatibleClient


def _map_reasoning_effort(
    reasoning: OllamaReasoningConfig | None,
    provider_overrides: dict[str, Any] | None,
) -> str | None:
    """Map reasoning config to OpenAI-compat reasoning_effort string.

    Ollama-specific: supports provider_overrides.ollama_think (bool or effort).
    Falls back to 'medium' if enabled=True without explicit effort.
    """
    if provider_overrides:
        override = provider_overrides.get("ollama_think")
        if override is not None:
            if isinstance(override, bool):
                return "medium" if override else None
            if isinstance(override, str) and override in {"low", "medium", "high"}:
                return override
            raise ValueError(
                "provider_overrides.ollama_think must be bool or low/medium/high"
            )

    if reasoning is None:
        return None
    if reasoning.enabled is False:
        return None
    if reasoning.effort is not None:
        return reasoning.effort
    if reasoning.enabled is True:
        return "medium"
    return None


class OllamaClient(OpenAICompatibleClient):
    def __init__(self, config: OllamaConfig):
        super().__init__(
            model=config.model,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            request_timeout=config.request_timeout,
            reasoning_effort=_map_reasoning_effort(
                config.reasoning,
                config.provider_overrides,
            ),
            temperature=config.temperature,
        )

    def chat(
        self,
        messages: list[Message],
        response_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> str:
        request = self._build_request(messages, response_schema=response_schema, temperature=temperature)
        data = self._do_post(request)
        result = OpenAIResponse.model_validate(data)
        content = result.choices[0].message.content or ""
        if content.strip():
            return content
        # Ollama thinking fallback: some models place answer in `thinking`
        raw_msg = data.get("choices", [{}])[0].get("message", {})
        return raw_msg.get("thinking", "") or content

    def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        temperature: float | None = None,
    ) -> LLMResponse:
        request = self._build_request(messages, tools=tools, temperature=temperature)
        data = self._do_post(request)
        result = OpenAIResponse.model_validate(data)
        return self._parse_response(result)
