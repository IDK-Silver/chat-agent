from typing import Any

import httpx

from ...core.schema import AnthropicConfig
from ..reasoning import map_anthropic_thinking
from ..schema import (
    AnthropicContent,
    AnthropicMessagePayload,
    AnthropicResponse,
    AnthropicTextContent,
    AnthropicTool,
    AnthropicToolInputSchema,
    AnthropicToolResultContent,
    AnthropicToolUseContent,
    ContentPart,
    LLMResponse,
    Message,
    ToolCall,
    ToolDefinition,
)


class AnthropicClient:
    def __init__(self, config: AnthropicConfig):
        self.model = config.model
        self.api_key = config.api_key
        self.base_url = config.base_url
        self.max_tokens = config.max_tokens
        self.request_timeout = config.request_timeout
        self.thinking = map_anthropic_thinking(
            config.reasoning,
            provider_overrides=config.provider_overrides,
        )

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[AnthropicTool]:
        """Convert ToolDefinition list to Anthropic tools format."""
        result = []
        for tool in tools:
            schema = tool.to_json_schema()
            result.append(
                AnthropicTool(
                    name=tool.name,
                    description=tool.description,
                    input_schema=AnthropicToolInputSchema(
                        properties=schema["properties"],
                        required=schema["required"],
                    ),
                )
            )
        return result

    @staticmethod
    def _convert_content_parts_to_blocks(
        parts: list[ContentPart],
    ) -> list[dict[str, Any]]:
        """Convert ContentPart list to Anthropic content blocks."""
        blocks: list[dict[str, Any]] = []
        for part in parts:
            if part.type == "text" and part.text is not None:
                blocks.append({"type": "text", "text": part.text})
            elif part.type == "image" and part.data and part.media_type:
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": part.media_type,
                        "data": part.data,
                    },
                })
        return blocks

    def _convert_messages(
        self, messages: list[Message]
    ) -> tuple[str | None, list[AnthropicMessagePayload]]:
        """Convert Message list to Anthropic format. Returns (system, messages)."""
        system = None
        result: list[AnthropicMessagePayload] = []

        for m in messages:
            if m.role == "system":
                if isinstance(m.content, str):
                    system = m.content
                # Multimodal system messages not supported by Anthropic
            elif m.role == "tool":
                if isinstance(m.content, list):
                    # Multimodal tool result: wrap content blocks in tool_result
                    inner_blocks = self._convert_content_parts_to_blocks(m.content)
                    result.append(
                        AnthropicMessagePayload(
                            role="user",
                            content=[{
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id or "",
                                "content": inner_blocks,
                            }],
                        )
                    )
                else:
                    tool_result = AnthropicToolResultContent(
                        tool_use_id=m.tool_call_id or "",
                        content=m.content or "",
                    )
                    result.append(
                        AnthropicMessagePayload(role="user", content=[tool_result])
                    )
            elif m.role == "assistant" and m.tool_calls:
                content_blocks: list[AnthropicContent] = []
                if isinstance(m.content, str) and m.content:
                    content_blocks.append(AnthropicTextContent(text=m.content))
                for tc in m.tool_calls:
                    content_blocks.append(
                        AnthropicToolUseContent(
                            id=tc.id,
                            name=tc.name,
                            input=tc.arguments,
                        )
                    )
                result.append(
                    AnthropicMessagePayload(role="assistant", content=content_blocks)
                )
            else:
                if isinstance(m.content, list):
                    blocks = self._convert_content_parts_to_blocks(m.content)
                    result.append(
                        AnthropicMessagePayload(role=m.role, content=blocks)
                    )
                else:
                    result.append(
                        AnthropicMessagePayload(role=m.role, content=m.content or "")
                    )

        return system, result

    def _parse_response(self, response: AnthropicResponse) -> LLMResponse:
        """Parse Anthropic response into unified LLMResponse."""
        text_blocks: list[str] = []
        tool_calls = []

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
        return LLMResponse(content=content, tool_calls=tool_calls)

    def _serialize_messages(
        self, messages: list[AnthropicMessagePayload]
    ) -> list[dict[str, Any]]:
        """Serialize messages to JSON-compatible format."""
        result = []
        for m in messages:
            if isinstance(m.content, str):
                result.append({"role": m.role, "content": m.content})
            else:
                # Content is a list of content blocks (Pydantic models or dicts)
                content_list = []
                for block in m.content:
                    if isinstance(block, dict):
                        content_list.append(block)
                    else:
                        content_list.append(block.model_dump())
                result.append({"role": m.role, "content": content_list})
        return result

    def chat(
        self,
        messages: list[Message],
        response_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> str:
        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        system, chat_messages = self._convert_messages(messages)

        request_data: dict[str, Any] = {
            "model": self.model,
            "messages": self._serialize_messages(chat_messages),
            "max_tokens": self.max_tokens,
        }
        if system:
            request_data["system"] = system
        if self.thinking:
            request_data["thinking"] = self.thinking
        if temperature is not None:
            request_data["temperature"] = temperature

        with httpx.Client(timeout=self.request_timeout) as client:
            response = client.post(url, headers=headers, json=request_data)
            response.raise_for_status()
            data = response.json()

        result = AnthropicResponse.model_validate(data)
        # Concatenate all text blocks in-order.
        text_blocks: list[str] = []
        for block in result.content:
            if block.type == "text" and block.text:
                text_blocks.append(block.text)
        return "".join(text_blocks)

    def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        temperature: float | None = None,
    ) -> LLMResponse:
        """Send messages with tool definitions and return response."""
        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        system, chat_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools) if tools else None

        request_data: dict[str, Any] = {
            "model": self.model,
            "messages": self._serialize_messages(chat_messages),
            "max_tokens": self.max_tokens,
        }
        if system:
            request_data["system"] = system
        if anthropic_tools:
            request_data["tools"] = [t.model_dump() for t in anthropic_tools]
        if self.thinking:
            request_data["thinking"] = self.thinking
        if temperature is not None:
            request_data["temperature"] = temperature

        with httpx.Client(timeout=self.request_timeout) as client:
            response = client.post(url, headers=headers, json=request_data)
            response.raise_for_status()
            data = response.json()

        result = AnthropicResponse.model_validate(data)
        return self._parse_response(result)
