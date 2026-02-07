"""Utilities for extracting JSON objects from imperfect LLM output."""

import json
import re
from typing import Any

_JSON_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def extract_json_object(raw: str) -> dict[str, Any] | None:
    """Extract the first valid JSON object from raw LLM output."""
    text = raw.strip()
    if not text:
        return None

    for candidate in _iter_candidates(text):
        data = _decode_candidate(candidate)
        if isinstance(data, dict):
            return data

    return None


def _iter_candidates(text: str):
    """Yield progressively weaker candidates likely to contain JSON."""
    yield text

    for match in _JSON_CODE_BLOCK_RE.finditer(text):
        block = match.group(1).strip()
        if block:
            yield block


def _decode_candidate(candidate: str) -> Any | None:
    """Decode JSON directly or by scanning for embedded JSON objects."""
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(candidate):
        if ch not in "{[":
            continue
        try:
            data, _ = decoder.raw_decode(candidate[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data

    return None
