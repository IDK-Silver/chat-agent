"""Upstream GitHub Copilot transport for the native project proxy."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any
from uuid import uuid4

import anyio
import httpx

from ..llm.schema import (
    ContentPart,
    CopilotNativeRequest,
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
from .settings import CopilotProxySettings


class CopilotUpstreamError(RuntimeError):
    """Wrap upstream HTTP errors while preserving raw response payload."""

    def __init__(self, *, status_code: int, body: str):
        super().__init__(body)
        self.status_code = status_code
        self.body = body


class CopilotTokenManager:
    """Cache and refresh short-lived Copilot bearer tokens."""

    def __init__(self, settings: CopilotProxySettings):
        self._settings = settings
        self._copilot_token: str | None = None
        self._expires_at: datetime | None = None
        self._lock = anyio.Lock()

    async def get_token(self) -> str:
        async with self._lock:
            if self._copilot_token and self._expires_at is not None:
                refresh_after = self._expires_at.timestamp() - 60
                if refresh_after > datetime.now(tz=UTC).timestamp():
                    return self._copilot_token

            async with httpx.AsyncClient(timeout=self._settings.request_timeout) as client:
                response = await client.get(
                    f"{self._settings.github_api_base_url}/copilot_internal/v2/token",
                    headers=self._github_headers(),
                )
            if response.status_code >= 400:
                raise CopilotUpstreamError(
                    status_code=response.status_code,
                    body=response.text,
                )

            payload = response.json()
            token = payload.get("token")
            expires_at = payload.get("expires_at")
            if not isinstance(token, str) or not token:
                raise RuntimeError("copilot_internal/v2/token returned no token")
            if not isinstance(expires_at, (int, float)):
                raise RuntimeError("copilot_internal/v2/token returned no expires_at")
            self._copilot_token = token
            self._expires_at = datetime.fromtimestamp(expires_at, tz=UTC)
            return token

    def _github_headers(self) -> dict[str, str]:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"token {self._settings.github_token}",
            "editor-version": f"vscode/{self._settings.editor_version}",
            "editor-plugin-version": self._settings.editor_plugin_version,
            "user-agent": self._settings.user_agent,
            "x-github-api-version": self._settings.api_version,
            "x-vscode-user-agent-library-version": "electron-fetch",
        }


class CopilotProxyService:
    """Translate native proxy requests into GitHub Copilot upstream calls."""

    def __init__(self, settings: CopilotProxySettings):
        self._settings = settings
        self._tokens = CopilotTokenManager(settings)

    async def chat(self, request: CopilotNativeRequest) -> LLMResponse:
        upstream_request = self._build_upstream_request(request)
        token = await self._tokens.get_token()
        async with httpx.AsyncClient(timeout=self._settings.request_timeout) as client:
            response = await client.post(
                f"{self._settings.copilot_base_url}/chat/completions",
                headers=self._copilot_headers(
                    token=token,
                    request_id=request.request_id,
                    interaction_id=request.interaction_id,
                    interaction_type=request.interaction_type or "conversation-agent",
                    initiator=request.initiator,
                    enable_vision=_request_has_images(request.messages),
                ),
                json=upstream_request.model_dump(exclude_none=True),
            )
        if response.status_code >= 400:
            raise CopilotUpstreamError(
                status_code=response.status_code,
                body=response.text,
            )
        return _parse_response(OpenAIResponse.model_validate(response.json()))

    def _build_upstream_request(self, request: CopilotNativeRequest) -> OpenAIRequest:
        upstream = OpenAIRequest(
            model=request.model,
            messages=_convert_messages(request.messages),
            max_tokens=request.max_tokens,
            tools=_convert_tools(request.tools) if request.tools else None,
            reasoning_effort=request.reasoning_effort,
            temperature=request.temperature,
        )
        if request.response_schema is not None:
            upstream.response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": False,
                    "schema": request.response_schema,
                },
            }
        return upstream

    def _copilot_headers(
        self,
        *,
        token: str,
        request_id: str | None,
        interaction_id: str | None,
        interaction_type: str,
        initiator: str,
        enable_vision: bool,
    ) -> dict[str, str]:
        resolved_request_id = request_id or uuid4().hex
        headers = {
            "Authorization": f"Bearer {token}",
            "content-type": "application/json",
            "copilot-integration-id": "vscode-chat",
            "editor-version": f"vscode/{self._settings.editor_version}",
            "editor-plugin-version": self._settings.editor_plugin_version,
            "user-agent": self._settings.user_agent,
            "openai-intent": "conversation-agent",
            "x-github-api-version": self._settings.api_version,
            "x-request-id": resolved_request_id,
            "x-agent-task-id": resolved_request_id,
            "x-vscode-user-agent-library-version": "electron-fetch",
            "x-initiator": initiator,
            "x-interaction-type": interaction_type,
        }
        if interaction_id:
            headers["x-interaction-id"] = interaction_id
        if enable_vision:
            headers["copilot-vision-request"] = "true"
        return headers


def _convert_tools(tools: list[ToolDefinition]) -> list[OpenAITool]:
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


def _repair_missing_tool_results(messages: list[Message]) -> list[Message]:
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


def _convert_content_parts(parts: list[ContentPart]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for part in parts:
        if part.type == "text" and part.text is not None:
            item: dict[str, Any] = {"type": "text", "text": part.text}
            if part.cache_control is not None:
                item["cache_control"] = part.cache_control
            result.append(item)
        elif part.type == "image" and part.data and part.media_type:
            result.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{part.media_type};base64,{part.data}",
                    },
                }
            )
    return result


def _convert_messages(messages: list[Message]) -> list[OpenAIMessagePayload]:
    messages = _repair_missing_tool_results(messages)
    result: list[OpenAIMessagePayload] = []
    pending_images: list[dict[str, Any]] = []
    for message in messages:
        if message.role != "tool" and pending_images:
            result.append(OpenAIMessagePayload(role="user", content=pending_images))
            pending_images = []

        if message.role == "tool":
            if isinstance(message.content, list):
                text_parts = [
                    part.text
                    for part in message.content
                    if part.type == "text" and part.text
                ]
                text_content = "\n".join(text_parts) if text_parts else ""
                image_blocks = [
                    block
                    for block in _convert_content_parts(message.content)
                    if block.get("type") == "image_url"
                ]
                result.append(
                    OpenAIMessagePayload(
                        role="tool",
                        content=text_content,
                        tool_call_id=message.tool_call_id,
                        name=message.name,
                    )
                )
                pending_images.extend(image_blocks)
            else:
                result.append(
                    OpenAIMessagePayload(
                        role="tool",
                        content=message.content,
                        tool_call_id=message.tool_call_id,
                        name=message.name,
                    )
                )
        elif message.role == "assistant" and message.tool_calls:
            openai_tool_calls = [
                OpenAIToolCall(
                    id=tool_call.id,
                    function=OpenAIFunctionCall(
                        name=tool_call.name,
                        arguments=json.dumps(tool_call.arguments),
                    ),
                )
                for tool_call in message.tool_calls
            ]
            result.append(
                OpenAIMessagePayload(
                    role="assistant",
                    content=message.content if isinstance(message.content, str) else None,
                    reasoning=message.reasoning_content if not message.reasoning_details else None,
                    reasoning_details=message.reasoning_details,
                    tool_calls=openai_tool_calls,
                )
            )
        else:
            if isinstance(message.content, list):
                result.append(
                    OpenAIMessagePayload(
                        role=message.role,
                        content=_convert_content_parts(message.content),
                    )
                )
            else:
                result.append(
                    OpenAIMessagePayload(role=message.role, content=message.content)
                )
    if pending_images:
        result.append(OpenAIMessagePayload(role="user", content=pending_images))
    return result


def _parse_response(response: OpenAIResponse) -> LLMResponse:
    content = None
    reasoning_parts: list[str] = []
    seen_reasoning: set[str] = set()
    reasoning_details: list[dict[str, Any]] | None = None
    tool_calls: list[ToolCall] = []
    finish_reason = None
    for choice in response.choices:
        message = choice.message
        if message.content and content is None:
            content = message.content
        if message.reasoning_content:
            chunk = message.reasoning_content.strip()
            if chunk and chunk not in seen_reasoning:
                seen_reasoning.add(chunk)
                reasoning_parts.append(chunk)
        if message.reasoning_details and reasoning_details is None:
            reasoning_details = message.reasoning_details
        if finish_reason is None:
            finish_reason = choice.finish_reason
        if message.tool_calls:
            for tool_call in message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tool_call.id,
                        name=tool_call.function.name,
                        arguments=json.loads(tool_call.function.arguments),
                    )
                )
    reasoning_content = "\n\n".join(reasoning_parts) if reasoning_parts else None

    cache_read = 0
    cache_write = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    usage_available = response.usage is not None
    if response.usage is not None:
        prompt_tokens = response.usage.prompt_tokens
        completion_tokens = response.usage.completion_tokens
        total_tokens = response.usage.total_tokens
        if response.usage.prompt_tokens_details is not None:
            cache_read = response.usage.prompt_tokens_details.cached_tokens
            cache_write = response.usage.prompt_tokens_details.cache_write_tokens

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


def _request_has_images(messages: list[Message]) -> bool:
    for message in messages:
        if not isinstance(message.content, list):
            continue
        for part in message.content:
            if part.type == "image":
                return True
    return False
