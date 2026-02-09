import json
import uuid

import httpx

from ...core.schema import OllamaConfig
from ..reasoning import map_ollama_think
from ..schema import (
    LLMResponse,
    Message,
    OllamaMessagePayload,
    OllamaRequest,
    OllamaResponse,
    OllamaToolCallPayload,
    OpenAIFunctionDef,
    OpenAITool,
    ToolCall,
    ToolDefinition,
)


class OllamaClient:
    def __init__(self, config: OllamaConfig):
        self.model = config.model
        self.base_url = config.base_url
        self.request_timeout = config.request_timeout
        self.think = map_ollama_think(
            config.reasoning,
            provider_overrides=config.provider_overrides,
        )

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[OpenAITool]:
        """Convert ToolDefinition list to Ollama tools format."""
        return [
            OpenAITool(
                function=OpenAIFunctionDef(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.to_json_schema(),
                )
            )
            for tool in tools
        ]

    def _convert_messages(self, messages: list[Message]) -> list[OllamaMessagePayload]:
        """Convert internal messages into Ollama /api/chat payload format."""
        result: list[OllamaMessagePayload] = []
        for m in messages:
            if m.role == "tool":
                result.append(
                    OllamaMessagePayload(
                        role="tool",
                        content=m.content,
                        tool_name=m.name,
                    )
                )
                continue

            if m.role == "assistant" and m.tool_calls:
                tool_calls: list[OllamaToolCallPayload] = [
                    OllamaToolCallPayload(
                        type="function",
                        function={
                            "name": tc.name,
                            "arguments": tc.arguments,
                        },
                    )
                    for tc in m.tool_calls
                ]
                result.append(
                    OllamaMessagePayload(
                        role="assistant",
                        content=m.content,
                        tool_calls=tool_calls,
                    )
                )
                continue

            result.append(OllamaMessagePayload(role=m.role, content=m.content))
        return result

    def _parse_response(self, response: OllamaResponse) -> LLMResponse:
        """Parse Ollama /api/chat response into unified LLMResponse."""
        message = response.message
        tool_calls: list[ToolCall] = []

        if message.tool_calls:
            for tc in message.tool_calls:
                arguments = tc.function.arguments
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}

                tool_calls.append(
                    ToolCall(
                        id=f"ollama-{uuid.uuid4()}",
                        name=tc.function.name,
                        arguments=arguments,
                    )
                )

        return LLMResponse(content=message.content, tool_calls=tool_calls)

    def _contains_tool_history(self, messages: list[Message]) -> bool:
        """Return True when request includes prior structured tool exchange."""
        return any(
            msg.role == "tool" or (msg.role == "assistant" and msg.tool_calls)
            for msg in messages
        )

    def _flatten_messages_for_text_fallback(
        self,
        messages: list[Message],
    ) -> list[OllamaMessagePayload]:
        """Convert tool history into plain text messages for resilient fallback."""
        flattened: list[OllamaMessagePayload] = []
        for msg in messages:
            if msg.role == "tool":
                tool_name = msg.name or "tool"
                content = msg.content or ""
                flattened.append(
                    OllamaMessagePayload(
                        role="user",
                        content=f"[{tool_name} result]\n{content}",
                    )
                )
                continue

            if msg.role == "assistant" and msg.tool_calls:
                called = ", ".join(tc.name for tc in msg.tool_calls)
                text = (msg.content or "").strip()
                if called:
                    suffix = f"[called tools: {called}]"
                    text = f"{text}\n{suffix}".strip() if text else suffix
                flattened.append(
                    OllamaMessagePayload(
                        role="assistant",
                        content=text or "",
                    )
                )
                continue

            flattened.append(OllamaMessagePayload(role=msg.role, content=msg.content))
        return flattened

    def _chat_text_fallback(self, messages: list[Message]) -> LLMResponse:
        """Fallback to plain text chat when structured tool history triggers provider 500."""
        url = f"{self.base_url}/api/chat"
        request = OllamaRequest(
            model=self.model,
            messages=self._flatten_messages_for_text_fallback(messages),
            think=self.think,
        )

        with httpx.Client(timeout=self.request_timeout) as client:
            response = client.post(url, json=request.model_dump(exclude_none=True))
            response.raise_for_status()
            data = response.json()

        result = OllamaResponse.model_validate(data)
        content = result.message.content or ""
        if not content.strip() and result.message.thinking:
            content = result.message.thinking
        return LLMResponse(content=content, tool_calls=[])

    def chat(self, messages: list[Message]) -> str:
        url = f"{self.base_url}/api/chat"

        request = OllamaRequest(
            model=self.model,
            messages=self._convert_messages(messages),
            think=self.think,
        )

        with httpx.Client(timeout=self.request_timeout) as client:
            response = client.post(url, json=request.model_dump(exclude_none=True))
            response.raise_for_status()
            data = response.json()

        result = OllamaResponse.model_validate(data)
        content = result.message.content or ""
        if content.strip():
            return content

        # Some reasoning models place the usable answer in `thinking`
        # while leaving `content` empty.
        if result.message.thinking:
            return result.message.thinking

        return content

    def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> LLMResponse:
        """Send messages with tool definitions using Ollama /api/chat."""
        url = f"{self.base_url}/api/chat"

        request = OllamaRequest(
            model=self.model,
            messages=self._convert_messages(messages),
            think=self.think,
            tools=self._convert_tools(tools) if tools else None,
        )

        try:
            with httpx.Client(timeout=self.request_timeout) as client:
                response = client.post(url, json=request.model_dump(exclude_none=True))
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 500 and self._contains_tool_history(messages):
                return self._chat_text_fallback(messages)
            raise

        result = OllamaResponse.model_validate(data)
        return self._parse_response(result)
