from typing import Any

from ...core.schema import OllamaConfig
from ..reasoning import map_ollama_reasoning_effort
from ..schema import (
    LLMResponse,
    Message,
    OpenAIResponse,
    ToolDefinition,
)
from .openai_compat import OpenAICompatibleClient


class OllamaClient(OpenAICompatibleClient):
    def __init__(self, config: OllamaConfig):
        super().__init__(
            model=config.model,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            request_timeout=config.request_timeout,
            reasoning_effort=map_ollama_reasoning_effort(
                config.reasoning,
                provider_overrides=config.provider_overrides,
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
