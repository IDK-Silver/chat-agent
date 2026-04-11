"""Upstream ChatGPT Codex transport for the native project proxy."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any

import anyio
import httpx

from chat_agent.llm.schema import (
    ContentPart,
    CodexNativeRequest,
    LLMResponse,
    Message,
    ToolCall,
    ToolDefinition,
    make_tool_result_message,
)

from .auth import (
    CodexAuthLoader,
    CodexTokenStore,
    StoredCodexToken,
    extract_chatgpt_account_id,
    extract_token_expiry,
    is_token_fresh,
    normalize_bearer_token,
)
from .settings import CodexProxySettings


class CodexUpstreamError(RuntimeError):
    """Wrap upstream HTTP errors while preserving raw response payload."""

    def __init__(self, *, status_code: int, body: str, media_type: str = "application/json"):
        super().__init__(body)
        self.status_code = status_code
        self.body = body
        self.media_type = media_type


class CodexTokenManager:
    """Load, cache, and refresh Codex OAuth tokens."""

    def __init__(self, settings: CodexProxySettings):
        self._settings = settings
        self._token_store = CodexTokenStore(settings.token_path)
        self._codex_auth = CodexAuthLoader(path=settings.codex_auth_path)
        self._token: StoredCodexToken | None = None
        self._lock = anyio.Lock()

    async def get_token(self) -> StoredCodexToken:
        if self._settings.access_token:
            return self._build_env_token(self._settings.access_token)

        async with self._lock:
            if self._token is not None and is_token_fresh(self._token):
                return self._token

            errors: list[str] = []
            stored = self._load_store(errors)
            if stored is not None and is_token_fresh(stored):
                return self._cache_and_return(stored)

            if stored is not None and stored.refresh_token:
                try:
                    refreshed = await self._refresh(stored.refresh_token)
                    self._token_store.save(refreshed)
                    return self._cache_and_return(refreshed)
                except Exception as exc:
                    errors.append(f"stored refresh failed: {exc}")

            imported = self._load_codex_auth(errors)
            if imported is not None and is_token_fresh(imported):
                self._token_store.save(imported)
                return self._cache_and_return(imported)

            if imported is not None and imported.refresh_token:
                try:
                    refreshed = await self._refresh(imported.refresh_token)
                    self._token_store.save(refreshed)
                    return self._cache_and_return(refreshed)
                except Exception as exc:
                    errors.append(f"codex auth refresh failed: {exc}")

            detail = "; ".join(errors) if errors else "no token source available"
            raise RuntimeError(
                "Codex OAuth token is required. Set CODEX_PROXY_ACCESS_TOKEN, "
                "run `uv run codex-proxy login`, run "
                "`uv run codex-proxy login --from-codex`, "
                "or enable Codex auth fallback. "
                f"({detail})"
            )

    def _cache_and_return(self, token: StoredCodexToken) -> StoredCodexToken:
        self._token = token
        return token

    def _build_env_token(self, token: str) -> StoredCodexToken:
        access_token = normalize_bearer_token(token)
        return StoredCodexToken(
            access_token=access_token,
            refresh_token=None,
            account_id=extract_chatgpt_account_id(access_token),
            expires_at=extract_token_expiry(access_token),
            source="env_override",
            client_id=self._settings.oauth_client_id,
            created_at=datetime.now(tz=UTC),
        )

    def _load_store(self, errors: list[str]) -> StoredCodexToken | None:
        try:
            return self._token_store.load()
        except ValueError as exc:
            errors.append(str(exc))
            return None

    def _load_codex_auth(self, errors: list[str]) -> StoredCodexToken | None:
        if not self._settings.allow_codex_auth_fallback:
            return None
        try:
            return self._codex_auth.load()
        except ValueError as exc:
            errors.append(str(exc))
            return None

    async def _refresh(self, refresh_token: str) -> StoredCodexToken:
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._settings.oauth_client_id,
        }
        async with httpx.AsyncClient(timeout=self._settings.request_timeout) as client:
            response = await client.post(
                self._settings.oauth_token_url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"OAuth refresh failed with status {response.status_code}: {response.text}"
            )
        data = response.json()
        access_token = data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise RuntimeError("OAuth refresh returned no access_token")
        next_refresh_token = data.get("refresh_token")
        if next_refresh_token is not None and not isinstance(next_refresh_token, str):
            raise RuntimeError("OAuth refresh returned invalid refresh_token")
        return StoredCodexToken(
            access_token=access_token,
            refresh_token=next_refresh_token or refresh_token,
            account_id=extract_chatgpt_account_id(access_token),
            expires_at=extract_token_expiry(access_token),
            source="oauth_refresh",
            client_id=self._settings.oauth_client_id,
            created_at=datetime.now(tz=UTC),
        )


class CodexProxyService:
    """Translate native proxy requests into ChatGPT Codex backend calls."""

    def __init__(self, settings: CodexProxySettings):
        self._settings = settings
        self._tokens = CodexTokenManager(settings)

    async def chat(self, request: CodexNativeRequest) -> LLMResponse:
        token = await self._tokens.get_token()
        payload = self._build_upstream_request(request)
        async with httpx.AsyncClient(timeout=self._settings.request_timeout) as client:
            response = await client.post(
                f"{self._settings.codex_base_url}/codex/responses",
                headers=self._headers(token),
                json=payload,
            )
        if response.status_code >= 400:
            raise CodexUpstreamError(
                status_code=response.status_code,
                body=response.text,
                media_type=response.headers.get("content-type", "application/json"),
            )
        return _parse_sse_response(response.text)

    def _build_upstream_request(self, request: CodexNativeRequest) -> dict[str, Any]:
        upstream: dict[str, Any] = {
            "model": request.model,
            "instructions": _extract_system_instructions(request.messages),
            "input": _convert_messages(request.messages),
            "stream": True,
            "store": False,
        }
        if not upstream["input"]:
            upstream["input"] = [{"type": "message", "role": "user", "content": ""}]
        # The ChatGPT Codex backend currently rejects top-level max_output_tokens
        # with HTTP 400 for all verified OAuth models. Keep the native field for
        # local compatibility, but do not forward it upstream.
        if request.reasoning_effort is not None:
            upstream["reasoning"] = {
                "effort": request.reasoning_effort,
                "summary": "auto",
            }
        if request.tools:
            upstream["tools"] = _convert_tools(request.tools)
            upstream["tool_choice"] = "auto"
        if request.response_schema is not None:
            upstream["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "response",
                    "schema": request.response_schema,
                    "strict": False,
                }
            }
        if request.temperature is not None:
            upstream["temperature"] = request.temperature
        if request.prompt_cache_key:
            # Reverse-engineered from:
            # https://github.com/icebear0828/codex-proxy (prompt_cache_key transport)
            upstream["prompt_cache_key"] = request.prompt_cache_key
        return upstream

    def _headers(self, token: StoredCodexToken) -> dict[str, str]:
        # Reverse-engineered from:
        # https://github.com/insightflo/chatgpt-codex-proxy (src/codex/client.ts)
        # https://github.com/icebear0828/codex-proxy
        return {
            "Authorization": f"Bearer {token.access_token}",
            "chatgpt-account-id": token.account_id,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
        }


def _convert_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.to_json_schema(),
        }
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


def _extract_system_instructions(messages: list[Message]) -> str:
    chunks: list[str] = []
    for message in messages:
        if message.role != "system":
            continue
        if isinstance(message.content, str) and message.content:
            chunks.append(message.content)
        elif isinstance(message.content, list):
            for part in message.content:
                if part.type == "text" and part.text:
                    chunks.append(part.text)
    return "\n\n".join(chunks)


def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
    repaired = _repair_missing_tool_results(messages)
    result: list[dict[str, Any]] = []
    pending_images: list[dict[str, Any]] = []

    for message in repaired:
        if message.role == "system":
            continue

        if message.role != "tool" and pending_images:
            result.append({"type": "message", "role": "user", "content": pending_images})
            pending_images = []

        if message.role == "tool":
            result.append(_convert_tool_result(message, pending_images))
            continue

        if message.role == "assistant" and message.tool_calls:
            converted = _convert_regular_message(message)
            if converted is not None:
                result.append(converted)
            for tool_call in message.tool_calls:
                result.append(
                    {
                        "type": "function_call",
                        "call_id": tool_call.id,
                        "name": tool_call.name,
                        "arguments": json.dumps(tool_call.arguments),
                    }
                )
            continue

        converted = _convert_regular_message(message)
        if converted is not None:
            result.append(converted)

    if pending_images:
        result.append({"type": "message", "role": "user", "content": pending_images})

    return result


def _convert_tool_result(
    message: Message,
    pending_images: list[dict[str, Any]],
) -> dict[str, Any]:
    if isinstance(message.content, list):
        text_parts = [
            part.text
            for part in message.content
            if part.type == "text" and part.text
        ]
        pending_images.extend(_extract_image_parts(message.content))
        output = "\n".join(text_parts) if text_parts else ""
    else:
        output = message.content or ""
    return {
        "type": "function_call_output",
        "call_id": message.tool_call_id or "tool_call",
        "output": output,
    }


def _convert_regular_message(message: Message) -> dict[str, Any] | None:
    role = "assistant" if message.role == "assistant" else "user"
    if isinstance(message.content, str):
        if not message.content and message.role == "assistant" and message.tool_calls:
            return None
        return {
            "type": "message",
            "role": role,
            "content": message.content or "",
        }
    if not isinstance(message.content, list):
        return None

    text_type = "output_text" if role == "assistant" else "input_text"
    parts: list[dict[str, Any]] = []
    for part in message.content:
        if part.type == "text" and part.text:
            parts.append({"type": text_type, "text": part.text})
        elif part.type == "image" and part.data and part.media_type and role == "user":
            parts.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{part.media_type};base64,{part.data}",
                }
            )
    if not parts:
        return None
    text_only = all(part["type"] == text_type for part in parts)
    if text_only:
        return {
            "type": "message",
            "role": role,
            "content": "\n".join(part["text"] for part in parts),
        }
    return {
        "type": "message",
        "role": role,
        "content": parts,
    }


def _extract_image_parts(parts: list[ContentPart]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for part in parts:
        if part.type == "image" and part.data and part.media_type:
            result.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{part.media_type};base64,{part.data}",
                }
            )
    return result


def _parse_sse_response(raw: str) -> LLMResponse:
    output_items: list[dict[str, Any]] = []
    final_response: dict[str, Any] | None = None
    output_text_chunks: list[str] = []
    reasoning_chunks: list[str] = []

    for data in _iter_sse_data(raw):
        if data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except ValueError:
            continue
        event_type = event.get("type")
        if event_type == "response.output_text.delta" and isinstance(event.get("delta"), str):
            output_text_chunks.append(event["delta"])
            continue
        if event_type in {"response.reasoning_summary_text.delta", "response.reasoning.delta"}:
            delta = event.get("delta")
            if isinstance(delta, str):
                reasoning_chunks.append(delta)
            continue
        if event_type == "response.output_item.done" and isinstance(event.get("item"), dict):
            output_items.append(event["item"])
            continue
        if event_type in {"response.completed", "response.done"} and isinstance(event.get("response"), dict):
            final_response = event["response"]
            continue
        if event_type in {"response.failed", "error"}:
            raise RuntimeError(_format_event_error(event))

    if final_response is not None:
        response_output = final_response.get("output")
        if not output_items and isinstance(response_output, list):
            output_items = [item for item in response_output if isinstance(item, dict)]

    content, reasoning_content, reasoning_details, tool_calls = _parse_output_items(
        output_items,
        fallback_text="".join(output_text_chunks) or None,
        fallback_reasoning="".join(reasoning_chunks) or None,
    )
    prompt_tokens, completion_tokens, total_tokens, cache_read_tokens, usage_available = _parse_usage(
        final_response.get("usage") if isinstance(final_response, dict) else None
    )
    finish_reason = "tool_calls" if tool_calls else _resolve_finish_reason(final_response)

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
        cache_read_tokens=cache_read_tokens,
    )


def _iter_sse_data(raw: str) -> list[str]:
    events: list[str] = []
    normalized = raw.replace("\r\n", "\n")
    for block in normalized.split("\n\n"):
        lines = [line for line in block.split("\n") if line.startswith("data:")]
        if not lines:
            continue
        payload = "\n".join(line[5:].lstrip() for line in lines)
        if payload:
            events.append(payload)
    return events


def _parse_output_items(
    output_items: list[dict[str, Any]],
    *,
    fallback_text: str | None,
    fallback_reasoning: str | None,
) -> tuple[str | None, str | None, list[dict[str, Any]] | None, list[ToolCall]]:
    content = None
    reasoning_parts: list[str] = []
    seen_reasoning: set[str] = set()
    reasoning_details: list[dict[str, Any]] = []
    tool_calls: list[ToolCall] = []

    for item in output_items:
        item_type = item.get("type")
        if item_type == "message":
            message_text = _extract_message_text(item)
            if message_text and content is None:
                content = message_text
            continue
        if item_type == "function_call":
            raw_arguments = item.get("arguments")
            tool_calls.append(
                ToolCall(
                    id=_string_or(item.get("call_id"), item.get("id"), default="tool_call"),
                    name=_string_or(item.get("name"), default="tool"),
                    arguments=_parse_arguments(raw_arguments),
                    provider_roundtrip=item,
                )
            )
            continue

        reasoning_text = _extract_reasoning_text(item)
        if reasoning_text:
            cleaned = reasoning_text.strip()
            if cleaned and cleaned not in seen_reasoning:
                seen_reasoning.add(cleaned)
                reasoning_parts.append(cleaned)
        reasoning_details.append(item)

    if content is None and fallback_text:
        content = fallback_text
    if fallback_reasoning:
        cleaned = fallback_reasoning.strip()
        if cleaned and cleaned not in seen_reasoning:
            reasoning_parts.append(cleaned)
    return (
        content,
        "\n\n".join(reasoning_parts) if reasoning_parts else None,
        reasoning_details or None,
        tool_calls,
    )


def _extract_message_text(item: dict[str, Any]) -> str | None:
    content = item.get("content")
    if isinstance(content, str):
        return content or None
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        text = part.get("text")
        if part_type in {"output_text", "text", "input_text"} and isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts) if parts else None


def _extract_reasoning_text(item: dict[str, Any]) -> str | None:
    text = item.get("text")
    if isinstance(text, str) and str(item.get("type", "")).startswith("reasoning"):
        return text
    summary = item.get("summary")
    if isinstance(summary, list):
        texts = [
            entry.get("text")
            for entry in summary
            if isinstance(entry, dict) and isinstance(entry.get("text"), str)
        ]
        if texts:
            return "\n".join(texts)
    content = item.get("content")
    if isinstance(content, list):
        texts = [
            part.get("text")
            for part in content
            if isinstance(part, dict)
            and isinstance(part.get("text"), str)
            and str(part.get("type", "")).startswith(("summary", "reasoning"))
        ]
        if texts:
            return "\n".join(texts)
    return None


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw_arguments": raw}
    return parsed if isinstance(parsed, dict) else {"_raw_arguments": raw}


def _parse_usage(
    usage: Any,
) -> tuple[int | None, int | None, int | None, int, bool]:
    if not isinstance(usage, dict):
        return None, None, None, 0, False
    prompt_tokens = _int_or_none(usage.get("input_tokens"))
    completion_tokens = _int_or_none(usage.get("output_tokens"))
    total_tokens = _int_or_none(usage.get("total_tokens"))
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens
    input_details = usage.get("input_tokens_details")
    cache_read = 0
    if isinstance(input_details, dict):
        cache_read = _int_or_none(input_details.get("cached_tokens")) or 0
    return prompt_tokens, completion_tokens, total_tokens, cache_read, True


def _resolve_finish_reason(final_response: dict[str, Any] | None) -> str | None:
    if not isinstance(final_response, dict):
        return "stop"
    stop_reason = final_response.get("stop_reason")
    if isinstance(stop_reason, str) and stop_reason:
        return stop_reason
    status = final_response.get("status")
    if isinstance(status, str) and status:
        return "stop" if status == "completed" else status
    return "stop"


def _format_event_error(event: dict[str, Any]) -> str:
    error = event.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    message = event.get("message")
    if isinstance(message, str) and message:
        return message
    return json.dumps(event)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _string_or(*values: Any, default: str) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return default
