"""Client for the project-native Copilot proxy API."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx

from ...core.schema import CopilotConfig
from ..schema import (
    ContextLengthExceededError,
    CopilotNativeRequest,
    LLMResponse,
    Message,
    ToolDefinition,
)
from .copilot_runtime import CopilotDispatchMode, CopilotRuntime


class CopilotClient:
    """Client for the local native Copilot proxy."""

    def __init__(
        self,
        config: CopilotConfig,
        *,
        runtime: CopilotRuntime | None = None,
        dispatch_mode: CopilotDispatchMode = "first_user_then_agent",
    ):
        self.model = config.model
        self.base_url = config.base_url.rstrip("/")
        self.max_tokens = config.max_tokens
        self.request_timeout = config.request_timeout
        self.temperature = config.temperature
        self.reasoning_effort = config.reasoning.effort if config.reasoning else None
        self._runtime = runtime
        self._dispatch_mode = dispatch_mode

    def _build_request(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        response_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> CopilotNativeRequest:
        effective_temp = temperature if temperature is not None else self.temperature
        routing = self._resolve_routing()
        return CopilotNativeRequest(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            tools=tools,
            response_schema=response_schema,
            reasoning_effort=self.reasoning_effort,
            temperature=effective_temp,
            initiator=routing.initiator,
            interaction_id=routing.interaction_id,
            interaction_type=routing.interaction_type,
            request_id=routing.request_id,
        )

    def _resolve_routing(self):
        if self._runtime is not None:
            return self._runtime.resolve_request(self._dispatch_mode)
        from .copilot_runtime import CopilotRequestRouting

        interaction_type = (
            "conversation-agent"
            if self._dispatch_mode == "first_user_then_agent"
            else "conversation-subagent"
        )
        initiator = "user" if self._dispatch_mode == "first_user_then_agent" else "agent"
        return CopilotRequestRouting(
            initiator=initiator,
            interaction_id=uuid4().hex,
            interaction_type=interaction_type,
            request_id=uuid4().hex,
        )

    @staticmethod
    def _get_headers() -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _do_post(self, request: CopilotNativeRequest) -> LLMResponse:
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
                    if (
                        "max_prompt_tokens_exceeded" in body
                        or "context_length_exceeded" in body
                    ):
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
