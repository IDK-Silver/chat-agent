"""Base client for OpenAI-compatible chat completions APIs."""

import json
from typing import Any

import httpx

from ..schema import (
    ContentPart,
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
        temperature: float | None = None,
    ):
        self.model = model
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.request_timeout = request_timeout
        self.reasoning_effort = reasoning_effort
        self.reasoning_payload = reasoning_payload
        self.temperature = temperature

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

    @staticmethod
    def _repair_missing_tool_results(messages: list[Message]) -> list[Message]:
        """Ensure every assistant tool_call has immediate tool results.

        Some providers (e.g. Claude via copilot-api) reject histories where
        an assistant tool call is not followed by matching tool messages.
        This can happen after an interrupted turn persisted partial history.
        """
        repaired: list[Message] = []
        idx = 0
        while idx < len(messages):
            msg = messages[idx]
            repaired.append(msg)
            if msg.role != "assistant" or not msg.tool_calls:
                idx += 1
                continue

            expected = {
                tc.id: tc.name
                for tc in msg.tool_calls
                if tc.id
            }
            idx += 1
            while idx < len(messages) and messages[idx].role == "tool":
                tool_msg = messages[idx]
                repaired.append(tool_msg)
                if tool_msg.tool_call_id in expected:
                    expected.pop(tool_msg.tool_call_id, None)
                idx += 1

            for missing_id, missing_name in expected.items():
                repaired.append(
                    Message(
                        role="tool",
                        content="[Recovered missing tool result]",
                        tool_call_id=missing_id,
                        name=missing_name,
                    )
                )
        return repaired

    @staticmethod
    def _convert_content_parts(parts: list[ContentPart]) -> list[dict[str, Any]]:
        """Convert ContentPart list to OpenAI content array format."""
        result: list[dict[str, Any]] = []
        for part in parts:
            if part.type == "text" and part.text is not None:
                item: dict[str, Any] = {"type": "text", "text": part.text}
                if part.cache_control is not None:
                    item["cache_control"] = part.cache_control
                result.append(item)
            elif part.type == "image" and part.data and part.media_type:
                result.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{part.media_type};base64,{part.data}",
                    },
                })
        return result

    def _convert_messages(self, messages: list[Message]) -> list[OpenAIMessagePayload]:
        messages = self._repair_missing_tool_results(messages)
        result = []
        # Collect images from tool results; flush as user message
        # after all consecutive tool messages in a group.
        pending_images: list[dict[str, Any]] = []
        for m in messages:
            # Flush pending images before any non-tool message
            if m.role != "tool" and pending_images:
                result.append(OpenAIMessagePayload(
                    role="user", content=pending_images,
                ))
                pending_images = []

            if m.role == "tool":
                if isinstance(m.content, list):
                    # Tool results: text goes in tool message,
                    # images deferred to a user message.
                    text_parts = [
                        p.text for p in m.content
                        if p.type == "text" and p.text
                    ]
                    text_content = "\n".join(text_parts) if text_parts else ""
                    image_blocks = [
                        b for b in self._convert_content_parts(m.content)
                        if b.get("type") == "image_url"
                    ]
                    result.append(
                        OpenAIMessagePayload(
                            role="tool",
                            content=text_content,
                            tool_call_id=m.tool_call_id,
                            name=m.name,
                        )
                    )
                    pending_images.extend(image_blocks)
                else:
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
                # Assistant content is always str
                result.append(
                    OpenAIMessagePayload(
                        role="assistant",
                        content=m.content if isinstance(m.content, str) else None,
                        reasoning=m.reasoning_content,
                        tool_calls=openai_tool_calls,
                    )
                )
            else:
                if isinstance(m.content, list):
                    result.append(OpenAIMessagePayload(
                        role=m.role,
                        content=self._convert_content_parts(m.content),
                    ))
                else:
                    result.append(OpenAIMessagePayload(role=m.role, content=m.content))
        # Flush any remaining images (tool results at end of conversation)
        if pending_images:
            result.append(OpenAIMessagePayload(role="user", content=pending_images))
        return result

    def _parse_response(self, response: OpenAIResponse) -> LLMResponse:
        # Merge all choices: some proxies (e.g. copilot-api for Claude) split
        # content and tool_calls into separate choices.
        content = None
        reasoning_parts: list[str] = []
        seen_reasoning: set[str] = set()
        tool_calls = []
        finish_reason = None
        for choice in response.choices:
            msg = choice.message
            if msg.content and content is None:
                content = msg.content
            if msg.reasoning_content:
                chunk = msg.reasoning_content.strip()
                if chunk and chunk not in seen_reasoning:
                    seen_reasoning.add(chunk)
                    reasoning_parts.append(chunk)
            if finish_reason is None:
                finish_reason = choice.finish_reason
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls.append(
                        ToolCall(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=json.loads(tc.function.arguments),
                        )
                    )
        reasoning_content = "\n\n".join(reasoning_parts) if reasoning_parts else None
        return LLMResponse(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )

    def _build_request(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        response_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> OpenAIRequest:
        effective_temp = temperature if temperature is not None else self.temperature
        request = OpenAIRequest(
            model=self.model,
            messages=self._convert_messages(messages),
            max_tokens=self.max_tokens,
            tools=self._convert_tools(tools) if tools else None,
            reasoning_effort=self.reasoning_effort,
            reasoning=self.reasoning_payload,
            temperature=effective_temp,
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
        temperature: float | None = None,
    ) -> str:
        request = self._build_request(messages, response_schema=response_schema, temperature=temperature)
        data = self._do_post(request)
        result = OpenAIResponse.model_validate(data)
        return self._parse_response(result).content or ""

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
