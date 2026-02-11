"""Retry wrapper for transient LLM client failures."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, TypeVar
import logging
import time

import httpx
from pydantic import ValidationError

from .base import LLMClient
from .schema import LLMResponse, MalformedFunctionCallError, Message, ToolDefinition

T = TypeVar("T")
_429_BACKOFF_SCHEDULE = (5.0, 10.0, 20.0, 30.0, 30.0)

logger = logging.getLogger(__name__)


class RetryingLLMClient:
    """Wrap an LLM client and retry transient errors."""

    def __init__(
        self,
        client: LLMClient,
        timeout_retries: int,
        rate_limit_retries: int = 0,
    ):
        self._client = client
        self._timeout_retries = max(0, timeout_retries)
        self._rate_limit_retries = max(0, rate_limit_retries)

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
        timeout_attempts = 0
        rate_limit_attempts = 0
        while True:
            try:
                return fn()
            except Exception as exc:
                if _is_429_error(exc):
                    if rate_limit_attempts >= self._rate_limit_retries:
                        raise
                    sleep_secs = _429_sleep_seconds(exc, rate_limit_attempts)
                    rate_limit_attempts += 1
                    logger.debug(
                        "429 retry %d/%d, sleeping %.1fs",
                        rate_limit_attempts,
                        self._rate_limit_retries,
                        sleep_secs,
                    )
                    time.sleep(sleep_secs)
                    continue

                if _is_retryable_exception(exc):
                    if timeout_attempts >= self._timeout_retries:
                        raise
                    timeout_attempts += 1
                    sleep_secs = _retry_sleep_seconds()
                    if sleep_secs > 0:
                        time.sleep(sleep_secs)
                    continue

                raise


def with_timeout_retry(
    client: LLMClient,
    timeout_retries: int,
    rate_limit_retries: int = 0,
) -> LLMClient:
    """Return a client wrapped with timeout retry behavior."""
    if timeout_retries <= 0 and rate_limit_retries <= 0:
        return client
    return RetryingLLMClient(client, timeout_retries, rate_limit_retries)


def _is_429_error(exc: Exception) -> bool:
    """Return True if the exception is an HTTP 429 rate limit error."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response is not None and exc.response.status_code == 429
    return False


def _is_retryable_exception(exc: Exception) -> bool:
    """Return True for transient exceptions that can succeed on retry.

    Note: 429 is handled separately via _is_429_error.
    """
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
        return status_code in {400, 500, 502, 503, 504}

    return False


def _429_sleep_seconds(exc: Exception, attempt: int) -> float:
    """Compute sleep seconds for a 429 error using fixed backoff schedule."""
    schedule_secs = _429_BACKOFF_SCHEDULE[min(attempt, len(_429_BACKOFF_SCHEDULE) - 1)]

    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        retry_after = _parse_retry_after_seconds(
            exc.response.headers.get("Retry-After")
        )
        if retry_after is not None:
            return max(retry_after, schedule_secs)

    return schedule_secs


def _retry_sleep_seconds() -> float:
    """Compute retry wait seconds for non-429 retryable errors."""
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
