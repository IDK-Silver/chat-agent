"""Retry wrapper for LLM clients."""

from typing import Any, Callable, TypeVar

import httpx

from .base import LLMClient
from .schema import LLMResponse, Message, ToolDefinition

T = TypeVar("T")


class RetryingLLMClient:
    """Wrap an LLM client and retry timeout errors."""

    def __init__(self, client: LLMClient, timeout_retries: int):
        self._client = client
        self._timeout_retries = max(0, timeout_retries)

    def chat(self, messages: list[Message]) -> str:
        return self._run_with_retry(lambda: self._client.chat(messages))

    def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> LLMResponse:
        return self._run_with_retry(
            lambda: self._client.chat_with_tools(messages, tools)
        )

    def _run_with_retry(self, fn: Callable[[], T]) -> T:
        for attempt in range(self._timeout_retries + 1):
            try:
                return fn()
            except (httpx.TimeoutException, TimeoutError):
                if attempt >= self._timeout_retries:
                    raise

        raise RuntimeError("unreachable")


def with_timeout_retry(client: LLMClient, timeout_retries: int) -> LLMClient:
    """Return a client wrapped with timeout retry behavior."""
    if timeout_retries <= 0:
        return client
    return RetryingLLMClient(client, timeout_retries)
