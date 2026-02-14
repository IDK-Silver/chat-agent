"""Base client for OpenAI-compatible chat completions APIs."""

import json
from typing import Any

import httpx

from ..schema import (
    ContextLengthExceededError,
    LLMResponse,
    Message,
    OpenAIFunctionCall,
    OpenAIFunctionDef,
    OpenAIMessagePayload,
    OpenAIRequest,
    OpenAIResponse,
    OpenAITool,
    OpenAIToolCall,
    ToolCall,
    ToolDefinition,
)


class OpenAICompatibleClient:
    """Base class for providers using OpenAI-compatible /chat/completions."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        max_tokens: int | None = None,
        request_timeout: float,
        reasoning_effort: str | None = None,
        reasoning_payload: dict[str, Any] | None = None,
    ):
        self.model = model
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.request_timeout = request_timeout
        self.reasoning_effort = reasoning_effort
        self.reasoning_payload = reasoning_payload

    def _get_headers(self) -> dict[str, str]:
        """Return request headers. Subclasses override to add auth."""
        return {"Content-Type": "application/json"}

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[OpenAITool]:
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

    def _build_request(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> OpenAIRequest:
        request = OpenAIRequest(
            model=self.model,
            messages=self._convert_messages(messages),
            max_tokens=self.max_tokens,
            tools=self._convert_tools(tools) if tools else None,
            reasoning_effort=self.reasoning_effort,
            reasoning=self.reasoning_payload,
        )
        if response_schema is not None:
            request.response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": False,
                    "schema": response_schema,
                },
            }
        return request

    def _do_post(self, request: OpenAIRequest) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        with httpx.Client(timeout=self.request_timeout) as client:
            response = client.post(
                url,
                headers=self._get_headers(),
                json=request.model_dump(exclude_none=True),
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400:
                    body = exc.response.text
                    if (
                        "max_prompt_tokens_exceeded" in body
                        or "context_length_exceeded" in body
                    ):
                        raise ContextLengthExceededError(body) from None
                raise
            return response.json()

    def chat(
        self,
        messages: list[Message],
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        request = self._build_request(messages, response_schema=response_schema)
        data = self._do_post(request)
        result = OpenAIResponse.model_validate(data)
        return result.choices[0].message.content or ""

    def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> LLMResponse:
        request = self._build_request(messages, tools=tools)
        data = self._do_post(request)
        result = OpenAIResponse.model_validate(data)
        return self._parse_response(result)
