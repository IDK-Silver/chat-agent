"""External web search tool backed by Tavily."""

from __future__ import annotations

import re
from collections.abc import Iterable

import httpx

from ...llm.schema import ToolDefinition, ToolParameter

_TAVILY_SEARCH_URL = "https://api.tavily.com/search"
_DEFAULT_RESULT_EXCERPT_CHARS = 500

WEB_SEARCH_DEFINITION = ToolDefinition(
    name="web_search",
    description=(
        "Search the public web for current or external facts. "
        "Use this for latest/current information, official docs, prices, schedules, "
        "policies, OAuth/auth flows, and third-party product behavior."
    ),
    parameters={
        "query": ToolParameter(
            type="string",
            description="Search query describing the fact or source you need.",
        ),
        "max_results": ToolParameter(
            type="integer",
            description="Maximum number of search results to return.",
        ),
        "include_domains": ToolParameter(
            type="array",
            description="Optional domains to restrict the search to.",
            items={"type": "string"},
        ),
        "time_range": ToolParameter(
            type="string",
            description="Optional recency filter for fresher results.",
            enum=["day", "week", "month", "year"],
        ),
    },
    required=["query"],
)


def _normalize_excerpt(text: str, *, max_chars: int = _DEFAULT_RESULT_EXCERPT_CHARS) -> str:
    """Normalize whitespace and bound excerpt size."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return "(no summary)"
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _normalize_domains(raw: Iterable[object] | None) -> list[str]:
    """Accept only non-empty string domains."""
    if raw is None:
        return []
    domains: list[str] = []
    for item in raw:
        if isinstance(item, str):
            value = item.strip()
            if value:
                domains.append(value)
    return domains


def _format_results(query: str, results: list[dict]) -> str:
    """Format Tavily results into compact plain text for the model."""
    lines = [f"Search results for: {query} ({len(results)} results)"]
    if not results:
        lines.append("No results found.")
        return "\n".join(lines)

    for i, item in enumerate(results, start=1):
        title = item.get("title") or "(untitled)"
        url = item.get("url") or "?"
        content = item.get("content") or item.get("raw_content") or ""
        excerpt = _normalize_excerpt(str(content))
        lines.append(f"[{i}] {title}")
        lines.append(f"    {url}")
        lines.append(f"    {excerpt}")
        if i != len(results):
            lines.append("")
    return "\n".join(lines)


def create_web_search(
    *,
    api_key: str,
    timeout: float = 10.0,
    default_max_results: int = 5,
    max_results_limit: int = 5,
    include_raw_content: bool = False,
):
    """Create a Tavily-backed web_search tool."""

    def web_search(
        query: str = "",
        max_results: int | None = None,
        include_domains: list[str] | None = None,
        time_range: str | None = None,
        **kwargs,
    ) -> str:
        del kwargs
        q = query.strip()
        if not q:
            return "Error: query is required."

        if max_results is None:
            effective_max_results = default_max_results
        elif not isinstance(max_results, int) or max_results < 1:
            return "Error: max_results must be a positive integer."
        else:
            effective_max_results = min(max_results, max_results_limit)

        if include_domains is not None and not isinstance(include_domains, list):
            return "Error: include_domains must be a list of strings."

        domains = _normalize_domains(include_domains)
        if include_domains is not None and not domains and include_domains:
            return "Error: include_domains must contain non-empty strings."

        allowed_time_ranges = {"day", "week", "month", "year"}
        if time_range is not None and time_range not in allowed_time_ranges:
            return "Error: time_range must be one of: day, week, month, year."

        payload = {
            "query": q,
            "topic": "general",
            "max_results": effective_max_results,
            "include_raw_content": include_raw_content,
        }
        if domains:
            payload["include_domains"] = domains
        if time_range is not None:
            payload["time_range"] = time_range

        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(_TAVILY_SEARCH_URL, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.TimeoutException:
            return "Error: Search timed out."
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in {401, 403}:
                return "Error: Invalid TAVILY_API_KEY."
            if status == 429:
                return "Error: Search rate limit exceeded, try again later."
            return f"Error: Search failed ({status})."
        except httpx.HTTPError as exc:
            return f"Error: Search failed ({exc})."

        try:
            data = response.json()
        except ValueError:
            return "Error: Search returned invalid JSON."

        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            return "Error: Search returned an invalid result format."

        results = [item for item in raw_results if isinstance(item, dict)]
        return _format_results(q, results)

    return web_search
