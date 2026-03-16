"""Read-only public URL fetch tool backed by httpx."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from ...llm.schema import ToolDefinition, ToolParameter

_DEFAULT_MAX_CHARS = 4000
_DEFAULT_MAX_BYTES = 300_000
_MIN_MAX_CHARS = 200
_DEFAULT_USER_AGENT = "chat-agent-web-fetch/1.0"
_IGNORED_HTML_TAGS = {"script", "style", "noscript", "template"}

WEB_FETCH_DEFINITION = ToolDefinition(
    name="web_fetch",
    description=(
        "Fetch a specific public URL when you already know the page to inspect. "
        "Use this for docs, articles, API responses, and public pages after web_search "
        "or when the user gives a URL directly. Not for login flows or complex browser interaction."
    ),
    parameters={
        "url": ToolParameter(
            type="string",
            description="Public http or https URL to fetch.",
        ),
        "max_chars": ToolParameter(
            type="integer",
            description="Optional maximum number of characters to return.",
            json_schema={"minimum": _MIN_MAX_CHARS},
        ),
    },
    required=["url"],
)


def _normalize_whitespace(text: str) -> str:
    """Collapse repeated whitespace for compact tool output."""
    return re.sub(r"\s+", " ", text or "").strip()


def _truncate_text(text: str, *, max_chars: int) -> tuple[str, bool]:
    """Bound output size while preserving readability."""
    normalized = text.strip()
    if len(normalized) <= max_chars:
        return normalized, False
    return normalized[: max_chars - 3].rstrip() + "...", True


def _extract_charset(content_type: str) -> str | None:
    """Extract a charset token from Content-Type when present."""
    match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1)


def _is_forbidden_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Reject local-only destinations for safety."""
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _validate_public_host(hostname: str) -> str | None:
    """Block obvious SSRF targets before issuing any request."""
    normalized = hostname.strip().lower().rstrip(".")
    if not normalized:
        return "url must include a host."
    if normalized in {"localhost", "localhost.localdomain"} or normalized.endswith(".local"):
        return "local hosts are not allowed."

    try:
        literal = ipaddress.ip_address(normalized)
    except ValueError:
        try:
            resolved = socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return None
        for _, _, _, _, sockaddr in resolved:
            if not sockaddr:
                continue
            try:
                address = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                continue
            if _is_forbidden_ip(address):
                return "private or local addresses are not allowed."
        return None

    if _is_forbidden_ip(literal):
        return "private or local addresses are not allowed."
    return None


def _classify_content_type(content_type: str, body: bytes) -> str:
    """Classify the payload into a supported render mode."""
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime in {"text/html", "application/xhtml+xml"}:
        return "html"
    if mime.startswith("text/"):
        return "text"
    if mime == "application/json" or mime.endswith("+json"):
        return "json"
    if mime in {"application/xml", "text/xml"} or mime.endswith("+xml"):
        return "text"
    if mime in {"application/javascript", "text/javascript"}:
        return "text"

    sample = body.lstrip()[:64].lower()
    if sample.startswith((b"<!doctype html", b"<html")):
        return "html"
    if sample.startswith((b"{", b"[")):
        return "json"
    if _looks_like_text(body):
        return "text"
    return "unknown"


def _looks_like_text(body: bytes) -> bool:
    """Use a small heuristic to avoid decoding obvious binary payloads."""
    sample = body[:1024]
    if not sample:
        return True
    if b"\x00" in sample:
        return False

    allowed_controls = {9, 10, 13}
    printable = 0
    for byte in sample:
        if 32 <= byte <= 126 or byte >= 160 or byte in allowed_controls:
            printable += 1
    return printable / len(sample) >= 0.85


def _decode_body(body: bytes, content_type: str) -> str:
    """Decode text payloads with a small charset fallback chain."""
    encodings = []
    charset = _extract_charset(content_type)
    if charset:
        encodings.append(charset)
    encodings.extend(["utf-8", "utf-16", "latin-1"])

    for encoding in encodings:
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


class _HTMLSummaryParser(HTMLParser):
    """Extract a compact title, description, and visible text summary."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in _IGNORED_HTML_TAGS:
            self._ignored_depth += 1
        if normalized_tag == "title":
            self._in_title = True
        if normalized_tag != "meta":
            return

        attr_map = {key.lower(): value for key, value in attrs if key and value}
        meta_key = attr_map.get("property") or attr_map.get("name")
        content = attr_map.get("content")
        if meta_key and content:
            self.meta[meta_key.lower()] = _normalize_whitespace(content)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in _IGNORED_HTML_TAGS and self._ignored_depth > 0:
            self._ignored_depth -= 1
        if normalized_tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        text = _normalize_whitespace(data)
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
            return
        if self._ignored_depth == 0:
            self.text_parts.append(text)


def _extract_html_summary(html: str) -> tuple[str | None, str | None, str]:
    """Turn raw HTML into a compact text representation."""
    parser = _HTMLSummaryParser()
    parser.feed(html)
    parser.close()

    title = _normalize_whitespace(" ".join(parser.title_parts))
    if not title:
        title = parser.meta.get("og:title") or parser.meta.get("twitter:title")

    description = (
        parser.meta.get("description")
        or parser.meta.get("og:description")
        or parser.meta.get("twitter:description")
    )

    deduped_parts: list[str] = []
    for part in parser.text_parts:
        if deduped_parts and deduped_parts[-1] == part:
            continue
        deduped_parts.append(part)
    body_text = _normalize_whitespace(" ".join(deduped_parts))
    return title or None, description or None, body_text


def _render_payload(body: bytes, content_type: str) -> tuple[str | None, str | None, str]:
    """Render supported payloads into plain text output."""
    content_kind = _classify_content_type(content_type, body)
    decoded = _decode_body(body, content_type)

    if content_kind == "html":
        return _extract_html_summary(decoded)
    if content_kind == "json":
        try:
            parsed = json.loads(decoded)
        except json.JSONDecodeError:
            return None, None, _normalize_whitespace(decoded)
        return None, None, json.dumps(parsed, ensure_ascii=False, indent=2)
    if content_kind == "text":
        return None, None, decoded.strip()

    raise ValueError(f"Unsupported content type '{content_type or 'unknown'}'.")


def _format_fetch_result(
    *,
    requested_url: str,
    final_url: str,
    status_code: int,
    content_type: str,
    title: str | None,
    description: str | None,
    content: str,
    truncated: bool,
) -> str:
    """Format fetch output into a compact text block for the model."""
    lines = [
        f"Fetched: {requested_url}",
        f"Final URL: {final_url}",
        f"Status: {status_code}",
        f"Content-Type: {content_type or 'unknown'}",
    ]
    if title:
        lines.append(f"Title: {title}")
    if description:
        lines.append(f"Description: {description}")
    if truncated:
        lines.append("Truncated: yes")
    lines.append("")
    lines.append("Content:")
    lines.append(content if content else "(no text extracted)")
    return "\n".join(lines)


def create_web_fetch(
    *,
    timeout: float = 10.0,
    default_max_chars: int = _DEFAULT_MAX_CHARS,
    max_response_chars: int = _DEFAULT_MAX_CHARS,
    max_response_bytes: int = _DEFAULT_MAX_BYTES,
    user_agent: str = _DEFAULT_USER_AGENT,
    allow_private_hosts: bool = False,
):
    """Create an httpx-based web_fetch tool."""

    def web_fetch(url: str = "", max_chars: int | None = None, **kwargs) -> str:
        del kwargs
        target = url.strip()
        if not target:
            return "Error: url is required."

        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https"}:
            return "Error: url must use http or https."
        if not parsed.hostname:
            return "Error: url must include a host."
        if parsed.username or parsed.password:
            return "Error: url must not include credentials."

        if max_chars is None:
            effective_max_chars = default_max_chars
        elif not isinstance(max_chars, int) or max_chars < _MIN_MAX_CHARS:
            return f"Error: max_chars must be an integer >= {_MIN_MAX_CHARS}."
        else:
            effective_max_chars = min(max_chars, max_response_chars)

        if not allow_private_hosts:
            host_error = _validate_public_host(parsed.hostname)
            if host_error:
                return f"Error: {host_error}"

        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.1",
        }

        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                headers=headers,
            ) as client:
                with client.stream("GET", target) as response:
                    response.raise_for_status()
                    status_code = response.status_code
                    content_type = response.headers.get("content-type", "")
                    content_length = response.headers.get("content-length")
                    if content_length and content_length.isdigit():
                        if int(content_length) > max_response_bytes:
                            return (
                                "Error: Response too large "
                                f"({content_length} bytes > limit {max_response_bytes})."
                            )

                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > max_response_bytes:
                            return (
                                f"Error: Response exceeded {max_response_bytes} bytes."
                            )
                    final_url = str(response.url)
        except httpx.TimeoutException:
            return "Error: Fetch timed out."
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            return f"Error: Fetch failed ({status})."
        except httpx.HTTPError as exc:
            return f"Error: Fetch failed ({exc})."

        try:
            title, description, content = _render_payload(bytes(body), content_type)
        except ValueError as exc:
            return f"Error: {exc}"

        bounded_content, truncated = _truncate_text(
            content,
            max_chars=effective_max_chars,
        )
        return _format_fetch_result(
            requested_url=target,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type.split(";", 1)[0].strip().lower(),
            title=title,
            description=description,
            content=bounded_content,
            truncated=truncated,
        )

    return web_fetch
