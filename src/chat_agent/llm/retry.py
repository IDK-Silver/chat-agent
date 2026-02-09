"""Retry wrapper for transient LLM client failures."""

from typing import Any, Callable, TypeVar

import httpx

from .base import LLMClient
from .schema import LLMResponse, Message, ToolDefinition

T = TypeVar("T")


class RetryingLLMClient:
    """Wrap an LLM client and retry transient errors."""

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
            except Exception as exc:
                if not _is_retryable_exception(exc) or attempt >= self._timeout_retries:
                    raise

        raise RuntimeError("unreachable")


def with_timeout_retry(client: LLMClient, timeout_retries: int) -> LLMClient:
    """Return a client wrapped with timeout retry behavior."""
    if timeout_retries <= 0:
        return client
    return RetryingLLMClient(client, timeout_retries)


def _is_retryable_exception(exc: Exception) -> bool:
    """Return True for transient exceptions that can succeed on retry."""
    if isinstance(
        exc,
        (
            httpx.TimeoutException,
            TimeoutError,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.RemoteProtocolError,
        ),
    ):
        return True

    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code if exc.response is not None else None
        return status_code in {400, 429, 500, 502, 503, 504}

    return False
