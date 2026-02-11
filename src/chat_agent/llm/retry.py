"""Retry wrapper for transient LLM client failures."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, TypeVar
import time

import httpx
from pydantic import ValidationError

from .base import LLMClient
from .schema import LLMResponse, MalformedFunctionCallError, Message, ToolDefinition

T = TypeVar("T")
_MIN_RETRY_SLEEP_SECONDS = 0.25


class RetryingLLMClient:
    """Wrap an LLM client and retry transient errors."""

    def __init__(self, client: LLMClient, timeout_retries: int):
        self._client = client
        self._timeout_retries = max(0, timeout_retries)

    def chat(
        self,
        messages: list[Message],
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        return self._run_with_retry(
            lambda: self._client.chat(messages, response_schema=response_schema)
        )

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
                sleep_seconds = _retry_sleep_seconds(exc, attempt)
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)

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
            MalformedFunctionCallError,
            ValidationError,
        ),
    ):
        return True

    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code if exc.response is not None else None
        return status_code in {400, 429, 500, 502, 503, 504}

    return False


def _retry_sleep_seconds(exc: Exception, attempt: int) -> float:
    """Compute retry wait seconds for retryable errors."""
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        if response is not None and response.status_code == 429:
            retry_after = _parse_retry_after_seconds(response.headers.get("Retry-After"))
            if retry_after is not None:
                return max(_MIN_RETRY_SLEEP_SECONDS, retry_after)
            # No Retry-After header: use gentle exponential backoff.
            return min(4.0, max(_MIN_RETRY_SLEEP_SECONDS, 1.0 * (2**attempt)))
    return 0.0


def _parse_retry_after_seconds(raw: str | None) -> float | None:
    """Parse Retry-After header value to seconds."""
    if not raw:
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        seconds = None
    if seconds is not None:
        return seconds

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None

    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    delta = (retry_at - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delta)
