import json

import httpx

from ...core.schema import OllamaConfig
from ..schema import (
    LLMResponse,
    Message,
    OllamaMessagePayload,
    OllamaRequest,
    OllamaResponse,
    OpenAIFunctionDef,
    OpenAIResponse,
    OpenAITool,
    ToolCall,
    ToolDefinition,
)


class OllamaClient:
    def __init__(self, config: OllamaConfig):
        self.model = config.model
        self.base_url = config.base_url

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[OpenAITool]:
        """Convert ToolDefinition list to OpenAI-compatible tools format."""
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

    def _convert_messages_openai(self, messages: list[Message]) -> list[dict]:
        """Convert Message list to OpenAI-compatible format for /v1/chat/completions."""
        result = []
        for m in messages:
            if m.role == "tool":
                result.append(
                    {
                        "role": "tool",
                        "content": m.content,
                        "tool_call_id": m.tool_call_id,
                        "name": m.name,
                    }
                )
            elif m.role == "assistant" and m.tool_calls:
                tool_calls = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ]
                result.append(
                    {
                        "role": "assistant",
                        "content": m.content,
                        "tool_calls": tool_calls,
                    }
                )
            else:
                result.append({"role": m.role, "content": m.content})
        return result

    def _parse_response(self, response: OpenAIResponse) -> LLMResponse:
        """Parse OpenAI-compatible response into unified LLMResponse."""
        message = response.choices[0].message
        tool_calls = []

        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments),
                    )
                )

        return LLMResponse(content=message.content, tool_calls=tool_calls)

    def chat(self, messages: list[Message]) -> str:
        url = f"{self.base_url}/api/chat"

        request = OllamaRequest(
            model=self.model,
            messages=[
                OllamaMessagePayload(role=m.role, content=m.content or "")
                for m in messages
            ],
        )

        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, json=request.model_dump())
            response.raise_for_status()
            data = response.json()

        result = OllamaResponse.model_validate(data)
        return result.message.content

    def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> LLMResponse:
        """Send messages with tool definitions using OpenAI-compatible endpoint."""
        # Use OpenAI-compatible endpoint for tool use
        url = f"{self.base_url}/v1/chat/completions"

        request_data = {
            "model": self.model,
            "messages": self._convert_messages_openai(messages),
        }
        if tools:
            request_data["tools"] = [t.model_dump() for t in self._convert_tools(tools)]

        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, json=request_data)
            response.raise_for_status()
            data = response.json()

        result = OpenAIResponse.model_validate(data)
        return self._parse_response(result)
