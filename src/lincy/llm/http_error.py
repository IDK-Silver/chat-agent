"""HTTP error classification helpers for LLM provider calls."""

from __future__ import annotations

import json
import re

import httpx

_REQUEST_FORMAT_PATTERNS = (
    re.compile(r"missing a thought_signature", re.IGNORECASE),
    re.compile(r"function call is missing", re.IGNORECASE),
    re.compile(r"\bmissing\b.*\b(function|field|parameter|argument|part)\b", re.IGNORECASE),
    re.compile(r"\binvalid\b.*\b(function|field|parameter|argument|payload|history|part)\b", re.IGNORECASE),
    re.compile(r"\bmalformed\b", re.IGNORECASE),
    re.compile(r"\bunexpected\b.*\bfield\b", re.IGNORECASE),
)


def _extract_response_detail(exc: httpx.HTTPStatusError) -> str:
    """Extract a short textual detail from an HTTPStatusError response."""
    response = exc.response
    if response is None:
        return ""
    text = response.text.strip()
    if not text:
        return ""

    try:
        payload = json.loads(text)
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        for key in ("error", "message", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return " ".join(text.split())


def classify_http_status_error(exc: httpx.HTTPStatusError) -> str | None:
    """Return a short category label for user-visible HTTP errors."""
    response = exc.response
    if response is None:
        return None

    status = response.status_code
    detail = _extract_response_detail(exc)

    if status == 400:
        if any(pattern.search(detail) for pattern in _REQUEST_FORMAT_PATTERNS):
            return "request-format"
        return "provider-api"

    if status in {401, 403, 404, 409, 422}:
        return "provider-api"

    return None


def format_http_status_error(exc: httpx.HTTPStatusError) -> str:
    """Return a compact user-facing HTTP error summary."""
    response = exc.response
    status = response.status_code if response is not None else "unknown"
    category = classify_http_status_error(exc)
    detail = _extract_response_detail(exc)

    prefix = f"HTTP {status}"
    if category:
        prefix += f" ({category})"
    if detail:
        return f"{prefix}: {detail}"
    return prefix
