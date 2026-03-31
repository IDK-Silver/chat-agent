"""Client for the project-native Claude Code proxy API.

Preserves Claude-native block structure and cache_control markers so
ContextBuilder breakpoints survive the local proxy hop unchanged.
"""

from __future__ import annotations

from typing import Any

import httpx

from ...core.schema import ClaudeCodeConfig, ClaudeCodeThinkingConfig
from ...core.schema import ClaudeCodeOutputConfig
from ..schema import (
    AnthropicResponse,
    AnthropicTool,
    AnthropicToolInputSchema,
    ClaudeCodeMessagePayload,
    ClaudeCodeRequest,
    ContentPart,
    LLMResponse,
    Message,
    ToolCall,
    ToolDefinition,
)


def _map_thinking(thinking: ClaudeCodeThinkingConfig | None) -> dict[str, Any] | None:
    if thinking is None:
        return None
    payload: dict[str, Any] = {"type": thinking.type}
    budget_tokens = getattr(thinking, "budget_tokens", None)
    if budget_tokens is not None:
        payload["budget_tokens"] = budget_tokens
    return payload


def _has_thinking(thinking: ClaudeCodeThinkingConfig | None) -> bool:
    return thinking is not None and thinking.type != "disabled"


def _model_supports_max_effort(model: str) -> bool:
    return "opus-4-6" in model.lower()


def _map_output_config(
    output_config: ClaudeCodeOutputConfig | None,
    model: str,
) -> dict[str, Any] | None:
    if output_config is None or output_config.effort is None:
        return None
    effort = output_config.effort
    if effort == "max" and not _model_supports_max_effort(model):
        effort = "high"
    return {"effort": effort}


class ClaudeCodeClient:
    """Client for the local Claude Code proxy."""

    def __init__(self, config: ClaudeCodeConfig):
        self.model = config.model
        self.base_url = config.base_url.rstrip("/")
        self.max_tokens = config.max_tokens
        self.request_timeout = config.request_timeout
        self.temperature = config.temperature
        self.has_thinking = _has_thinking(config.thinking)
        self.thinking = _map_thinking(config.thinking)
        self.output_config = _map_output_config(config.output_config, config.model)

    @staticmethod
    def _get_headers() -> dict[str, str]:
        return {"Content-Type": "application/json"}

    @staticmethod
    def _apply_cache_control(
        block: dict[str, Any],
        part: ContentPart,
    ) -> dict[str, Any]:
        if part.cache_control is not None:
            block["cache_control"] = part.cache_control
        return block

    @classmethod
    def _convert_content_parts_to_blocks(
        cls,
        parts: list[ContentPart],
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for part in parts:
            if part.type == "text" and part.text is not None:
                blocks.append(
                    cls._apply_cache_control(
                        {
                            "type": "text",
                            "text": part.text,
                        },
                        part,
                    )
                )
            elif part.type == "image" and part.data and part.media_type:
                blocks.append(
                    cls._apply_cache_control(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": part.media_type,
                                "data": part.data,
                            },
                        },
                        part,
                    )
                )
        return blocks

    @classmethod
    def _message_to_text_block(cls, content: str) -> dict[str, Any]:
        return {"type": "text", "text": content}

    def _convert_messages(
        self,
        messages: list[Message],
    ) -> tuple[list[dict[str, Any]], list[ClaudeCodeMessagePayload]]:
        system_blocks: list[dict[str, Any]] = []
        converted: list[ClaudeCodeMessagePayload] = []

        for message in messages:
            if message.role == "system":
                if isinstance(message.content, list):
                    system_blocks.extend(self._convert_content_parts_to_blocks(message.content))
                elif isinstance(message.content, str) and message.content:
                    system_blocks.append(self._message_to_text_block(message.content))
                continue

            if message.role == "tool":
                if isinstance(message.content, list):
                    tool_content: str | list[dict[str, Any]] = [{
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id or "",
                        "content": self._convert_content_parts_to_blocks(message.content),
                    }]
                else:
                    tool_content = [{
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id or "",
                        "content": message.content or "",
                    }]
                converted.append(
                    ClaudeCodeMessagePayload(role="user", content=tool_content)
                )
                continue

            if message.role == "assistant" and message.tool_calls:
                blocks: list[dict[str, Any]] = []
                if isinstance(message.content, list):
                    blocks.extend(self._convert_content_parts_to_blocks(message.content))
                elif isinstance(message.content, str) and message.content:
                    blocks.append(self._message_to_text_block(message.content))
                for tool_call in message.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tool_call.id,
                            "name": tool_call.name,
                            "input": tool_call.arguments,
                        }
                    )
                converted.append(
                    ClaudeCodeMessagePayload(role="assistant", content=blocks)
                )
                continue

            if isinstance(message.content, list):
                content: str | list[dict[str, Any]] = self._convert_content_parts_to_blocks(
                    message.content
                )
            else:
                content = message.content or ""
            converted.append(
                ClaudeCodeMessagePayload(role=message.role, content=content)
            )

        return system_blocks, converted

    @staticmethod
    def _convert_tools(tools: list[ToolDefinition]) -> list[AnthropicTool]:
        converted: list[AnthropicTool] = []
        for tool in tools:
            schema = tool.to_json_schema()
            converted.append(
                AnthropicTool(
                    name=tool.name,
                    description=tool.description,
                    input_schema=AnthropicToolInputSchema(
                        properties=schema["properties"],
                        required=schema["required"],
                    ),
                )
            )
        return converted

    @staticmethod
    def _parse_response(response: AnthropicResponse) -> LLMResponse:
        text_blocks: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text" and block.text:
                text_blocks.append(block.text)
            elif block.type == "tool_use" and block.id and block.name:
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input or {},
                    )
                )

        content = "".join(text_blocks) if text_blocks else None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        total_tokens: int | None = None
        cache_read = 0
        cache_write = 0
        usage_available = response.usage is not None
        if response.usage is not None:
            base_input = response.usage.input_tokens
            cache_read = response.usage.cache_read_input_tokens or 0
            cache_write = response.usage.cache_creation_input_tokens or 0
            prompt_tokens = (base_input or 0) + cache_read + cache_write
            completion_tokens = response.usage.output_tokens
            total_tokens = prompt_tokens + (completion_tokens or 0)

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            usage_available=usage_available,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )

    def _build_request(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
    ) -> ClaudeCodeRequest:
        system_blocks, chat_messages = self._convert_messages(messages)
        effective_temp = temperature if temperature is not None else self.temperature
        return ClaudeCodeRequest(
            model=self.model,
            system=system_blocks or None,
            messages=chat_messages,
            max_tokens=self.max_tokens,
            tools=self._convert_tools(tools) if tools else None,
            thinking=self.thinking,
            output_config=self.output_config,
            temperature=effective_temp if not self.has_thinking else None,
        )

    def _post(self, request: ClaudeCodeRequest) -> AnthropicResponse:
        url = f"{self.base_url}/v1/messages"
        with httpx.Client(timeout=self.request_timeout) as client:
            response = client.post(
                url,
                headers=self._get_headers(),
                json=request.model_dump(exclude_none=True, by_alias=True),
            )
            response.raise_for_status()
        return AnthropicResponse.model_validate(response.json())

    def chat(
        self,
        messages: list[Message],
        response_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> str:
        if response_schema is not None:
            raise ValueError(
                "Claude Code provider does not support response_schema; "
                "use a provider with native structured outputs."
            )
        request = self._build_request(messages, temperature=temperature)
        response = self._post(request)
        return self._parse_response(response).content or ""

    def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        temperature: float | None = None,
    ) -> LLMResponse:
        request = self._build_request(
            messages,
            tools=tools,
            temperature=temperature,
        )
        response = self._post(request)
        return self._parse_response(response)
