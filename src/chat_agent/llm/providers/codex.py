"""Client for the project-native Codex proxy API."""

from __future__ import annotations

from typing import Any

import httpx

from ...core.schema import CodexConfig
from ..schema import (
    CodexNativeRequest,
    ContextLengthExceededError,
    LLMResponse,
    Message,
    ToolDefinition,
)


class CodexClient:
    """Client for the local native Codex proxy."""

    def __init__(self, config: CodexConfig):
        self.model = config.model
        self.base_url = config.base_url.rstrip("/")
        self.max_output_tokens = config.max_tokens
        self.request_timeout = config.request_timeout
        self.temperature = config.temperature
        self.reasoning_effort = config.reasoning.effort if config.reasoning else None

    def _build_request(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        response_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> CodexNativeRequest:
        effective_temp = temperature if temperature is not None else self.temperature
        return CodexNativeRequest(
            model=self.model,
            messages=messages,
            max_output_tokens=self.max_output_tokens,
            tools=tools,
            response_schema=response_schema,
            reasoning_effort=self.reasoning_effort,
            temperature=effective_temp,
        )

    @staticmethod
    def _get_headers() -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _do_post(self, request: CodexNativeRequest) -> LLMResponse:
        url = f"{self.base_url}/chat"
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
                    if "context_length_exceeded" in body:
                        raise ContextLengthExceededError(body) from None
                raise
            return LLMResponse.model_validate(response.json())

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
        response = self._do_post(request)
        return response.content or ""

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
        return self._do_post(request)
