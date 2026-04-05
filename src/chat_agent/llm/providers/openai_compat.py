"""Base client for OpenAI-compatible chat completions APIs."""

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

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
    make_tool_result_message,
)

# Trailing-comma before closing brace/bracket: {"key": "val",} or ["a",]
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _repair_json_arguments(raw: str) -> dict[str, Any]:
    """Best-effort repair of malformed tool-call argument JSON.

    Some models (e.g. Qwen 3.5 under long context) occasionally produce
    invalid JSON in tool call arguments.  Try common fixes before giving up.
    """
    # 1. Trailing commas
    fixed = _TRAILING_COMMA_RE.sub(r"\1", raw)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 2. Unquoted string values or single quotes
    fixed2 = raw.replace("'", '"')
    try:
        return json.loads(fixed2)
    except json.JSONDecodeError:
        pass

    # Give up: log and return the raw string as the sole argument
    # so the tool can still surface a meaningful error.
    logger.warning("Could not repair tool call arguments: %s", raw[:200])
    return {"_raw_arguments": raw}


def _filter_empty_thinking(
    details: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Drop thinking blocks with empty/whitespace-only text.

    Anthropic rejects 'each thinking block must contain non-whitespace
    thinking'.  This can happen when replaying history from providers
    (e.g. Qwen) that emit empty reasoning blocks.
    """
    if not details:
        return details
    filtered = [
        d for d in details
        if not (d.get("type", "").startswith("thinking") or d.get("type") == "reasoning.text")
        or (d.get("text") or "").strip()
    ]
    return filtered or None


class OpenAICompatibleClient:
    """Base class for providers using OpenAI-compatible /chat/completions."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        max_tokens: int | None = None,
        max_completion_tokens: int | None = None,
        request_timeout: float,
        reasoning_effort: str | None = None,
        reasoning_payload: dict[str, Any] | None = None,
        provider_payload: dict[str, Any] | None = None,
        temperature: float | None = None,
        prompt_cache_retention: str | None = None,
    ):
        self.model = model
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.max_completion_tokens = max_completion_tokens
        self.prompt_cache_retention = prompt_cache_retention
        self.request_timeout = request_timeout
        self.reasoning_effort = reasoning_effort
        self.reasoning_payload = reasoning_payload
        self.provider_payload = provider_payload
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

        Some OpenAI-style gateways (including Copilot-backed Claude routes)
        reject histories where
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
                    make_tool_result_message(
                        tool_call_id=missing_id,
                        name=missing_name,
                        content="[Recovered missing tool result]",
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
                # Assistant content is always str.
                # Prefer reasoning_details (structured) for cache-friendly round-trip;
                # fall back to reasoning (plain string) for non-Claude providers.
                # Use "" instead of None so exclude_none=True still emits
                # "content" -- some providers (Together) require the field.
                assistant_content = m.content if isinstance(m.content, str) else ""
                result.append(
                    OpenAIMessagePayload(
                        role="assistant",
                        content=assistant_content,
                        reasoning=m.reasoning_content if not m.reasoning_details else None,
                        reasoning_details=_filter_empty_thinking(m.reasoning_details),
                        tool_calls=openai_tool_calls,
                    )
                )
            else:
                if isinstance(m.content, list):
                    converted = self._convert_content_parts(m.content)
                    # Propagate Message-level cache_control to the last block
                    if m.cache_control and converted:
                        converted[-1]["cache_control"] = m.cache_control
                    result.append(OpenAIMessagePayload(
                        role=m.role,
                        content=converted,
                    ))
                elif m.cache_control:
                    # Wrap in content-block array to carry cache_control
                    result.append(OpenAIMessagePayload(
                        role=m.role,
                        content=[{"type": "text", "text": m.content or "", "cache_control": m.cache_control}],
                    ))
                else:
                    result.append(OpenAIMessagePayload(role=m.role, content=m.content))
        # Flush any remaining images (tool results at end of conversation)
        if pending_images:
            result.append(OpenAIMessagePayload(role="user", content=pending_images))
        return result

    def _parse_response(self, response: OpenAIResponse) -> LLMResponse:
        # Merge all choices: some OpenAI-style gateways split
        # content and tool_calls into separate choices.
        content = None
        reasoning_parts: list[str] = []
        seen_reasoning: set[str] = set()
        reasoning_details: list[dict[str, Any]] | None = None
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
            # Preserve structured reasoning_details for cache-friendly round-trip
            if msg.reasoning_details and reasoning_details is None:
                reasoning_details = msg.reasoning_details
            if finish_reason is None:
                finish_reason = choice.finish_reason
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = _repair_json_arguments(tc.function.arguments)
                    tool_calls.append(
                        ToolCall(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=args,
                        )
                    )
        reasoning_content = "\n\n".join(reasoning_parts) if reasoning_parts else None

        cache_read = 0
        cache_write = 0
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        total_tokens: int | None = None
        usage_available = response.usage is not None
        if response.usage:
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            total_tokens = response.usage.total_tokens
        if response.usage and response.usage.prompt_tokens_details:
            details = response.usage.prompt_tokens_details
            cache_read = details.cached_tokens
            cache_write = details.cache_write_tokens

        return LLMResponse(
            content=content,
            reasoning_content=reasoning_content,
            reasoning_details=reasoning_details,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
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
        response_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> OpenAIRequest:
        effective_temp = temperature if temperature is not None else self.temperature
        request = OpenAIRequest(
            model=self.model,
            messages=self._convert_messages(messages),
            max_tokens=self.max_tokens,
            max_completion_tokens=self.max_completion_tokens,
            tools=self._convert_tools(tools) if tools else None,
            reasoning_effort=self.reasoning_effort,
            reasoning=self.reasoning_payload,
            provider=self.provider_payload,
            temperature=effective_temp,
            prompt_cache_retention=self.prompt_cache_retention,
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
