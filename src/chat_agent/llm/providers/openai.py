import json

import httpx

from ...core.schema import OpenAIConfig
from ..schema import (
    LLMResponse,
    Message,
    OpenAIFunctionDef,
    OpenAIMessagePayload,
    OpenAIRequest,
    OpenAIResponse,
    OpenAITool,
    ToolCall,
    ToolDefinition,
)


class OpenAIClient:
    def __init__(self, config: OpenAIConfig):
        self.model = config.model
        self.api_key = config.api_key
        self.base_url = config.base_url
        self.max_tokens = config.max_tokens

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[OpenAITool]:
        """Convert ToolDefinition list to OpenAI tools format."""
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

    def _convert_messages(self, messages: list[Message]) -> list[OpenAIMessagePayload]:
        """Convert Message list to OpenAI message format."""
        result = []
        for m in messages:
            if m.role == "tool":
                result.append(
                    OpenAIMessagePayload(
                        role="tool",
                        content=m.content,
                        tool_call_id=m.tool_call_id,
                        name=m.name,
                    )
                )
            elif m.role == "assistant" and m.tool_calls:
                from ..schema import OpenAIFunctionCall, OpenAIToolCall

                openai_tool_calls = [
                    OpenAIToolCall(
                        id=tc.id,
                        function=OpenAIFunctionCall(
                            name=tc.name,
                            arguments=json.dumps(tc.arguments),
                        ),
                    )
                    for tc in m.tool_calls
                ]
                result.append(
                    OpenAIMessagePayload(
                        role="assistant",
                        content=m.content,
                        tool_calls=openai_tool_calls,
                    )
                )
            else:
                result.append(OpenAIMessagePayload(role=m.role, content=m.content))
        return result

    def _parse_response(self, response: OpenAIResponse) -> LLMResponse:
        """Parse OpenAI response into unified LLMResponse."""
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
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        request = OpenAIRequest(
            model=self.model,
            messages=self._convert_messages(messages),
            max_tokens=self.max_tokens,
        )

        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                url, headers=headers, json=request.model_dump(exclude_none=True)
            )
            response.raise_for_status()
            data = response.json()

        result = OpenAIResponse.model_validate(data)
        return result.choices[0].message.content or ""

    def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> LLMResponse:
        """Send messages with tool definitions and return response."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        request = OpenAIRequest(
            model=self.model,
            messages=self._convert_messages(messages),
            max_tokens=self.max_tokens,
            tools=self._convert_tools(tools) if tools else None,
        )

        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                url, headers=headers, json=request.model_dump(exclude_none=True)
            )
            response.raise_for_status()
            data = response.json()

        result = OpenAIResponse.model_validate(data)
        return self._parse_response(result)
