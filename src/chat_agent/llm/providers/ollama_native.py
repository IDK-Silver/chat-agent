"""Ollama native provider client.

Uses the native /api/chat endpoint with official think/tools/format/options
payloads. See docs/dev/provider-api-spec.md.
"""

import logging
from typing import Any

import httpx

from ...core.schema import OllamaNativeConfig
from ..schema import (
    ContentPart,
    ContextLengthExceededError,
    LLMResponse,
    MalformedFunctionCallError,
    Message,
    OllamaNativeFunctionCall,
    OllamaNativeFunctionDef,
    OllamaNativeMessagePayload,
    OllamaNativeRequest,
    OllamaNativeResponse,
    OllamaNativeTool,
    OllamaNativeToolCall,
    ToolCall,
    ToolDefinition,
    make_tool_result_message,
)

logger = logging.getLogger(__name__)


def _build_chat_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/api/chat"):
        return trimmed
    if trimmed.endswith("/api"):
        return f"{trimmed}/chat"
    return f"{trimmed}/api/chat"


def _map_thinking(config: OllamaNativeConfig) -> bool | str:
    thinking = config.thinking
    if thinking.mode == "toggle":
        return thinking.enabled
    return thinking.effort


class OllamaNativeClient:
    def __init__(self, config: OllamaNativeConfig):
        self.model = config.model
        self.base_url = config.base_url
        self.chat_url = _build_chat_url(config.base_url)
        self.max_tokens = config.max_tokens
        self.request_timeout = config.request_timeout
        self.temperature = config.temperature
        self.think = _map_thinking(config)

    def _get_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[OllamaNativeTool]:
        return [
            OllamaNativeTool(
                function=OllamaNativeFunctionDef(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.to_json_schema(),
                )
            )
            for tool in tools
        ]

    @staticmethod
    def _repair_missing_tool_results(messages: list[Message]) -> list[Message]:
        """Ensure every assistant tool_call has immediate named tool results."""
        repaired: list[Message] = []
        idx = 0
        while idx < len(messages):
            msg = messages[idx]
            repaired.append(msg)
            if msg.role != "assistant" or not msg.tool_calls:
                idx += 1
                continue

            expected = {tc.id: tc.name for tc in msg.tool_calls if tc.id}
            idx += 1
            while idx < len(messages) and messages[idx].role == "tool":
                tool_msg = messages[idx]
                if not tool_msg.name and tool_msg.tool_call_id:
                    repaired_name = expected.get(tool_msg.tool_call_id)
                    if repaired_name:
                        logger.debug(
                            "Repaired missing Ollama tool name: id=%s name=%s",
                            tool_msg.tool_call_id,
                            repaired_name,
                        )
                        tool_msg = make_tool_result_message(
                            tool_call_id=tool_msg.tool_call_id,
                            name=repaired_name,
                            content=tool_msg.content,
                            timestamp=tool_msg.timestamp,
                        )
                repaired.append(tool_msg)
                if tool_msg.tool_call_id in expected:
                    expected.pop(tool_msg.tool_call_id, None)
                idx += 1

            for missing_id, missing_name in expected.items():
                repaired.append(
                    make_tool_result_message(
                        tool_call_id=missing_id,
                        name=missing_name,
                        content="[Recovered missing tool result]",
                    )
                )
        return repaired

    @staticmethod
    def _split_content_parts(parts: list[ContentPart]) -> tuple[str | None, list[str]]:
        text_parts = [part.text for part in parts if part.type == "text" and part.text]
        images = [part.data for part in parts if part.type == "image" and part.data]
        text = "\n".join(text_parts) if text_parts else None
        return text, images

    def _convert_messages(self, messages: list[Message]) -> list[OllamaNativeMessagePayload]:
        messages = self._repair_missing_tool_results(messages)
        result: list[OllamaNativeMessagePayload] = []
        pending_tool_images: list[str] = []

        for message in messages:
            if message.role != "tool" and pending_tool_images:
                result.append(
                    OllamaNativeMessagePayload(
                        role="user",
                        images=pending_tool_images,
                    )
                )
                pending_tool_images = []

            content: str | None
            images: list[str]
            if isinstance(message.content, list):
                content, images = self._split_content_parts(message.content)
            else:
                content = message.content
                images = []

            if message.role == "tool":
                if not message.name:
                    raise ValueError("Ollama native tool messages require Message.name")
                result.append(
                    OllamaNativeMessagePayload(
                        role="tool",
                        content=content or "",
                        tool_name=message.name,
                    )
                )
                pending_tool_images.extend(images)
                continue

            payload = OllamaNativeMessagePayload(
                role=message.role,
                content=content,
                images=images or None,
            )
            if message.role == "assistant":
                if message.reasoning_content:
                    payload.thinking = message.reasoning_content
                if message.tool_calls:
                    payload.tool_calls = [
                        OllamaNativeToolCall(
                            function=OllamaNativeFunctionCall(
                                name=tool_call.name,
                                arguments=tool_call.arguments,
                            )
                        )
                        for tool_call in message.tool_calls
                    ]
            result.append(payload)

        if pending_tool_images:
            result.append(
                OllamaNativeMessagePayload(
                    role="user",
                    images=pending_tool_images,
                )
            )

        return result

    def _build_request(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        response_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> OllamaNativeRequest:
        options: dict[str, Any] = {}
        if self.max_tokens is not None:
            options["num_predict"] = self.max_tokens
        effective_temperature = temperature if temperature is not None else self.temperature
        if effective_temperature is not None:
            options["temperature"] = effective_temperature

        return OllamaNativeRequest(
            model=self.model,
            messages=self._convert_messages(messages),
            stream=False,
            tools=self._convert_tools(tools) if tools else None,
            format=response_schema,
            think=self.think,
            options=options or None,
        )

    def _do_post(self, request: OllamaNativeRequest) -> dict[str, Any]:
        with httpx.Client(timeout=self.request_timeout) as client:
            response = client.post(
                self.chat_url,
                headers=self._get_headers(),
                json=request.model_dump(exclude_none=True),
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400:
                    body = exc.response.text.lower()
                    if (
                        "context length" in body
                        or "context window" in body
                        or "max prompt tokens" in body
                    ):
                        raise ContextLengthExceededError(exc.response.text) from None
                raise
            return response.json()

    def _parse_response(self, response: OllamaNativeResponse) -> LLMResponse:
        tool_calls: list[ToolCall] = []
        for idx, tool_call in enumerate(response.message.tool_calls or []):
            name = tool_call.function.name.strip()
            if not name:
                raise MalformedFunctionCallError(
                    "MALFORMED_FUNCTION_CALL: Ollama returned a tool call with an empty name."
                )
            tool_calls.append(
                ToolCall(
                    id=f"ollama-tool-{idx + 1}",
                    name=name,
                    arguments=tool_call.function.arguments,
                )
            )
        prompt_tokens = response.prompt_eval_count
        completion_tokens = response.eval_count
        usage_available = prompt_tokens is not None or completion_tokens is not None
        total_tokens = None
        if usage_available:
            total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

        return LLMResponse(
            content=response.message.content or None,
            reasoning_content=response.message.thinking or None,
            tool_calls=tool_calls,
            finish_reason=response.done_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            usage_available=usage_available,
        )

    def chat(
        self,
        messages: list[Message],
        response_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> str:
        request = self._build_request(
            messages,
            response_schema=response_schema,
            temperature=temperature,
        )
        data = self._do_post(request)
        response = OllamaNativeResponse.model_validate(data)
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
        data = self._do_post(request)
        response = OllamaNativeResponse.model_validate(data)
        return self._parse_response(response)
