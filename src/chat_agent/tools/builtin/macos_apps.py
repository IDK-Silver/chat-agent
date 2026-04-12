"""macOS personal-app tools for Calendar, Reminders, Notes, and Photos."""

from __future__ import annotations

import base64
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import escape as html_escape
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any

from markdownify import markdownify

from ...llm.schema import ContentPart, Message, ToolDefinition, ToolParameter
from ..security import is_path_allowed

logger = logging.getLogger(__name__)

_SLOW_APP_TOOL_SECONDS = 5.0
_APPLE_NOTES_CACHE_VERSION = "2"
_APPLE_NOTES_DEFAULT_SEARCH_LIMIT = 5
_APPLE_NOTES_SUMMARY_MAX_INPUT_CHARS = 20_000
_APPLE_NOTES_MAX_NOTE_WORKERS = 4
_APPLE_NOTES_IMAGE_PROMPT = (
    "這是 Apple 備忘錄裡的內嵌圖片。"
    "請用繁體中文提取可讀文字，並簡短說明這張圖對筆記內容最重要的資訊。"
    "只回純文字，不要加前言，控制在 200 字內。"
)
_APPLE_NOTES_SUMMARY_SYSTEM_PROMPT = (
    "你是 Apple Notes 搜尋摘要器。"
    "請用繁體中文輸出 2 到 3 句短摘要，幫主模型快速判斷這則筆記值不值得打開。"
    "優先保留主題、關鍵名詞、時間、人名、待辦或決策。"
    "只回摘要，不要列點，不要補充多餘前言。"
)
_DATA_IMAGE_RE = re.compile(
    r"<img\b[^>]*\bsrc=(?P<quote>[\"'])(?P<src>data:image/[^\"']+)(?P=quote)[^>]*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_HREF_RE = re.compile(
    r"""href=(?P<quote>["'])(?P<href>https?://.+?)(?P=quote)""",
    flags=re.IGNORECASE | re.DOTALL,
)
_URL_TEXT_RE = re.compile(r"https?://[^\s<>\"]+")
_TEMPLATE_VAR_RE = re.compile(r"\{(?P<name>[A-Za-z0-9_]+)\}")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<ref>[A-Za-z0-9_]+)\)")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")
_ORDERED_LIST_RE = re.compile(r"^\s*\d+\.\s+(?P<body>.+)$")
_INLINE_URL_RE = re.compile(r"(?P<url>https?://[^\s<>\"]+)")
_NOTE_HEADING_BLOCK_RE = re.compile(
    r"""
    <div>\s*
    (?:
      <h(?P<h_level>[1-3])(?P<h_attrs>[^>]*)>(?P<h_body>.*?)</h(?P=h_level)>
      |
      <(?:b|strong)>\s*<span(?P<span_attrs>[^>]*)>(?P<span_body>.*?)</span>\s*</(?:b|strong)>
    )
    \s*(?:<br\s*/?>)?\s*
    </div>
    """,
    flags=re.IGNORECASE | re.DOTALL | re.VERBOSE,
)
_NOTE_IMAGE_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

CALENDAR_TOOL_DEFINITION = ToolDefinition(
    name="calendar_tool",
    description=(
        "Access the user's real macOS Calendar data. "
        "'catalog' lists calendars, "
        "'search' searches events by calendar/date/query, "
        "'conflicts' finds events that overlap a candidate time range, "
        "'get' fetches a single event by uid, "
        "'create' creates a new event, "
        "'update' updates an existing event by uid."
    ),
    parameters={
        "action": ToolParameter(
            type="string",
            description="Action to perform.",
            enum=["catalog", "search", "conflicts", "get", "create", "update"],
        ),
        "calendar": ToolParameter(
            type="string",
            description="Exact calendar name. Required for create; optional for search/update.",
        ),
        "calendars": ToolParameter(
            type="array",
            description="Optional exact calendar names. Use to search/check conflicts across multiple calendars.",
            items={"type": "string"},
        ),
        "event_uid": ToolParameter(
            type="string",
            description="Calendar event uid. Required for update.",
        ),
        "exclude_event_uid": ToolParameter(
            type="string",
            description="Optional event uid to exclude from conflict checks.",
        ),
        "query": ToolParameter(
            type="string",
            description="Case-insensitive text query matched against title, notes, and location.",
        ),
        "title": ToolParameter(
            type="string",
            description="Event title. Required for create.",
        ),
        "notes": ToolParameter(
            type="string",
            description="Event notes/description.",
        ),
        "location": ToolParameter(
            type="string",
            description="Event location.",
        ),
        "url": ToolParameter(
            type="string",
            description="Optional URL attached to the event.",
        ),
        "start": ToolParameter(
            type="string",
            description="Local ISO datetime, e.g. '2026-04-20T14:00'. Required for create.",
        ),
        "end": ToolParameter(
            type="string",
            description="Local ISO datetime, e.g. '2026-04-20T15:00'. Required for create.",
        ),
        "all_day": ToolParameter(
            type="boolean",
            description="Optional all-day filter for search/conflicts, or the all-day value for create/update.",
        ),
        "sort_by": ToolParameter(
            type="string",
            description="Sort order for search/conflicts results.",
            enum=["start_asc", "start_desc"],
        ),
        "limit": ToolParameter(
            type="integer",
            description="Maximum number of search results to return.",
        ),
    },
    required=["action"],
)

REMINDERS_TOOL_DEFINITION = ToolDefinition(
    name="reminders_tool",
    description=(
        "Access the user's real macOS Reminders data. "
        "'catalog' lists reminder lists, "
        "'search' searches reminders, "
        "'get' fetches one reminder by id, "
        "'create' creates a reminder, "
        "'update' updates a reminder by id, "
        "'complete' marks a reminder complete/incomplete."
    ),
    parameters={
        "action": ToolParameter(
            type="string",
            description="Action to perform.",
            enum=["catalog", "search", "get", "create", "update", "complete"],
        ),
        "list_id": ToolParameter(
            type="string",
            description="Reminder list id. Preferred over list_name when known.",
        ),
        "list_name": ToolParameter(
            type="string",
            description="Exact reminder list name.",
        ),
        "list_path": ToolParameter(
            type="string",
            description="Reminder list path from catalog, e.g. 'iCloud/Work'.",
        ),
        "reminder_id": ToolParameter(
            type="string",
            description="Reminder id. Required for update/complete.",
        ),
        "query": ToolParameter(
            type="string",
            description="Case-insensitive text query matched against title and notes.",
        ),
        "title": ToolParameter(
            type="string",
            description="Reminder title. Required for create.",
        ),
        "notes": ToolParameter(
            type="string",
            description="Reminder notes/body.",
        ),
        "due": ToolParameter(
            type="string",
            description="Local ISO datetime, e.g. '2026-04-20T09:00'.",
        ),
        "due_start": ToolParameter(
            type="string",
            description="Lower bound for reminder due date when searching.",
        ),
        "due_end": ToolParameter(
            type="string",
            description="Upper bound for reminder due date when searching.",
        ),
        "priority": ToolParameter(
            type="integer",
            description="Reminder priority: 0 none, 1-4 high, 5 medium, 6-9 low.",
        ),
        "priority_min": ToolParameter(
            type="integer",
            description="Optional minimum priority filter for search.",
        ),
        "priority_max": ToolParameter(
            type="integer",
            description="Optional maximum priority filter for search.",
        ),
        "flagged": ToolParameter(
            type="boolean",
            description="Whether the reminder is flagged. Search filter or write value.",
        ),
        "completed": ToolParameter(
            type="boolean",
            description="Whether the reminder is completed. Search filter or write value.",
        ),
        "sort_by": ToolParameter(
            type="string",
            description="Sort order for search results.",
            enum=["due_asc", "due_desc", "title_asc"],
        ),
        "limit": ToolParameter(
            type="integer",
            description="Maximum number of search results to return.",
        ),
    },
    required=["action"],
)

NOTES_TOOL_DEFINITION = ToolDefinition(
    name="notes_tool",
    description=(
        "Access the user's real macOS Notes data. "
        "'catalog' lists accounts and folder structure, "
        "'search' searches notes by folder/query, "
        "'get' fetches one note by id, "
        "'create' creates a note in a specific folder, "
        "'update' updates a note by id, "
        "'move' moves a note to another folder."
    ),
    parameters={
        "action": ToolParameter(
            type="string",
            description="Action to perform.",
            enum=["catalog", "search", "get", "create", "update", "move"],
        ),
        "account": ToolParameter(
            type="string",
            description="Exact Notes account name, such as 'iCloud'.",
        ),
        "folder_id": ToolParameter(
            type="string",
            description="Folder id. Preferred over folder_path when known.",
        ),
        "folder_path": ToolParameter(
            type="string",
            description="Folder path from catalog, e.g. 'iCloud/待讀'.",
        ),
        "target_folder_id": ToolParameter(
            type="string",
            description="Destination folder id for move.",
        ),
        "target_folder_path": ToolParameter(
            type="string",
            description="Destination folder path for move, e.g. 'iCloud/已讀'.",
        ),
        "note_id": ToolParameter(
            type="string",
            description="Note id. Required for get/update/move.",
        ),
        "query": ToolParameter(
            type="string",
            description="Case-insensitive text query matched against note title, rendered markdown, and cached summary.",
        ),
        "created_after": ToolParameter(
            type="string",
            description="Lower bound for note creation time when searching.",
        ),
        "created_before": ToolParameter(
            type="string",
            description="Upper bound for note creation time when searching.",
        ),
        "modified_after": ToolParameter(
            type="string",
            description="Lower bound for note modification time when searching.",
        ),
        "modified_before": ToolParameter(
            type="string",
            description="Upper bound for note modification time when searching.",
        ),
        "title": ToolParameter(
            type="string",
            description=(
                "Canonical note title. This controls the actual Notes note name. "
                "When title is provided, do not repeat the same text as the first "
                "Markdown heading unless you intentionally want a duplicated visible title."
            ),
        ),
        "body": ToolParameter(
            type="string",
            description="Plain note body content. Required for create/update unless template_markdown is used.",
        ),
        "template_markdown": ToolParameter(
            type="string",
            description=(
                "Optional Markdown template used to render the full note body. "
                "Supports #/##/### headings, paragraphs, bold/italic/code, lists, links, simple tables, and image placeholders. "
                "If title is already provided, body content should usually start at ## instead of repeating the same # heading."
            ),
        ),
        "variables": ToolParameter(
            type="object",
            description=(
                "Optional free-form text variables for template_markdown. "
                "Template placeholders like {title} or {summary} can use any key name."
            ),
            json_schema={"additionalProperties": {"type": "string"}},
        ),
        "images": ToolParameter(
            type="object",
            description=(
                "Optional free-form image variables for template_markdown. "
                "Use either {image_key} or ![alt](image_key) in the template, then map image_key to an absolute file path."
            ),
            json_schema={"additionalProperties": {"type": "string"}},
        ),
        "append": ToolParameter(
            type="boolean",
            description="When true, append body to the existing note instead of replacing it.",
        ),
        "sort_by": ToolParameter(
            type="string",
            description="Sort order for search results.",
            enum=["modified_desc", "modified_asc", "created_desc", "created_asc"],
        ),
        "limit": ToolParameter(
            type="integer",
            description="Maximum number of search results to return. Defaults to 5.",
        ),
        "offset": ToolParameter(
            type="integer",
            description="Zero-based page offset for search results.",
        ),
    },
    required=["action"],
)

PHOTOS_TOOL_DEFINITION = ToolDefinition(
    name="photos_tool",
    description=(
        "Access the user's real macOS Photos library. "
        "'catalog' lists albums/folders, "
        "'search' searches media by album/date/query/favorite, "
        "'get_album' fetches a single album by id, name, or path, "
        "'get_media' fetches media metadata by id, "
        "'export' exports media items to files, "
        "'create_album' creates an album, "
        "'add_to_album' adds media items to an album."
    ),
    parameters={
        "action": ToolParameter(
            type="string",
            description="Action to perform.",
            enum=[
                "catalog",
                "search",
                "get_album",
                "get_media",
                "export",
                "create_album",
                "add_to_album",
            ],
        ),
        "album_id": ToolParameter(
            type="string",
            description="Album id. Preferred over album_name when known.",
        ),
        "album_name": ToolParameter(
            type="string",
            description="Exact album name.",
        ),
        "album_path": ToolParameter(
            type="string",
            description="Album path from catalog, e.g. 'Trips/2026 Kyoto'.",
        ),
        "folder_id": ToolParameter(
            type="string",
            description="Photos folder id. Search scopes across albums inside that folder subtree.",
        ),
        "folder_path": ToolParameter(
            type="string",
            description="Photos folder path from catalog, e.g. 'Trips/2026'.",
        ),
        "parent_folder_id": ToolParameter(
            type="string",
            description="Optional parent Photos folder id when creating an album.",
        ),
        "parent_folder_path": ToolParameter(
            type="string",
            description="Optional parent Photos folder path when creating an album.",
        ),
        "query": ToolParameter(
            type="string",
            description="Case-insensitive text query matched against media title, filename, description, and keywords.",
        ),
        "start": ToolParameter(
            type="string",
            description="Local ISO datetime lower bound for media date.",
        ),
        "end": ToolParameter(
            type="string",
            description="Local ISO datetime upper bound for media date.",
        ),
        "favorite": ToolParameter(
            type="boolean",
            description="Filter by favorite flag.",
        ),
        "sort_by": ToolParameter(
            type="string",
            description="Sort order for search results.",
            enum=["date_desc", "date_asc", "filename_asc"],
        ),
        "media_ids": ToolParameter(
            type="array",
            description="List of Photos media item ids. Required for get_media/export/add_to_album.",
            items={"type": "string"},
        ),
        "destination_dir": ToolParameter(
            type="string",
            description="Export destination directory. Must be within allowed paths.",
        ),
        "use_originals": ToolParameter(
            type="boolean",
            description="When true, export original assets. Default true.",
        ),
        "limit": ToolParameter(
            type="integer",
            description="Maximum number of search results to return.",
        ),
    },
    required=["action"],
)


def _json_output(payload: dict[str, Any]) -> str:
    """Render tool output as stable JSON text."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _error(message: str) -> str:
    """Build a standard tool error string."""
    return f"Error: {message}"


def _parse_local_datetime(value: str, *, field_name: str) -> datetime:
    """Parse an ISO datetime string."""
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc


def _datetime_env(prefix: str, value: datetime) -> dict[str, str]:
    """Convert a datetime into AppleScript-friendly environment variables."""
    return {
        f"{prefix}_YEAR": str(value.year),
        f"{prefix}_MONTH": str(value.month),
        f"{prefix}_DAY": str(value.day),
        f"{prefix}_HOUR": str(value.hour),
        f"{prefix}_MINUTE": str(value.minute),
        f"{prefix}_SECOND": str(value.second),
    }


def _build_note_html(title: str | None, body: str) -> str:
    """Build a simple HTML payload accepted by Notes."""
    parts: list[str] = []
    if title:
        parts.append(f"<div><b>{html_escape(title)}</b></div>")
    for line in body.splitlines():
        if line.strip():
            parts.append(f"<div>{_linkify_escaped_urls(html_escape(line))}</div>")
        else:
            parts.append("<div><br></div>")
    return "".join(parts) or "<div><br></div>"


def _html_to_markdown(html: str) -> str:
    """Convert HTML content into readable Markdown."""
    normalized_html = _normalize_notes_heading_html(html)
    return markdownify(
        normalized_html,
        strip=["script", "style", "noscript", "template"],
        heading_style="ATX",
    ).strip()


def _heading_level_from_style(attrs: str, *, fallback: int | None = None) -> int | None:
    """Infer Notes heading level from normalized font size styles."""
    match = re.search(
        r"font-size\s*:\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>px|pt)",
        attrs or "",
        flags=re.IGNORECASE,
    )
    if not match:
        return fallback
    value = float(match.group("value"))
    unit = match.group("unit").lower()
    candidates = (
        {1: 20.0, 2: 18.0, 3: 16.0}
        if unit == "px"
        else {1: 15.0, 2: 13.5, 3: 12.0}
    )
    closest_level = min(candidates, key=lambda level: abs(candidates[level] - value))
    if abs(candidates[closest_level] - value) <= 0.6:
        return closest_level
    return fallback


def _normalize_notes_heading_html(html: str) -> str:
    """Convert Notes-normalized heading blocks back into semantic heading tags."""

    def replace(match: re.Match[str]) -> str:
        if match.group("h_level"):
            body = match.group("h_body") or ""
            level = _heading_level_from_style(
                match.group("h_attrs") or "",
                fallback=int(match.group("h_level")),
            )
        else:
            body = match.group("span_body") or ""
            level = _heading_level_from_style(match.group("span_attrs") or "")
        if level is None:
            return match.group(0)
        return f"<h{level}>{body}</h{level}>"

    return _NOTE_HEADING_BLOCK_RE.sub(replace, html)


def _normalize_markdown(text: str) -> str:
    """Collapse noisy blank lines and whitespace from Markdown output."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _first_visible_markdown_line(text: str) -> str:
    """Extract the first visible content line from Markdown-ish text."""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^(?:[-*]\s+|\d+\.\s+)", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            return line
    return ""


def _extract_source_url(html: str) -> str | None:
    """Extract the first http(s) link from a note body."""
    match = _HREF_RE.search(html)
    if match:
        return match.group("href").strip()
    text_match = _URL_TEXT_RE.search(html)
    return text_match.group(0).strip() if text_match else None


def _coerce_template_mapping(
    value: dict[str, Any] | None,
    *,
    field_name: str,
) -> dict[str, str]:
    """Normalize template variables/images into a string mapping."""
    if value is None:
        return {}
    normalized: dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{field_name} keys must be non-empty strings")
        if raw is None:
            normalized[key] = ""
            continue
        if isinstance(raw, (str, int, float, bool)):
            normalized[key] = str(raw)
            continue
        raise ValueError(f"{field_name}.{key} must be a string-like scalar")
    return normalized


def _split_table_row(line: str) -> list[str]:
    """Split one simple pipe-table row."""
    trimmed = line.strip()
    if trimmed.startswith("|"):
        trimmed = trimmed[1:]
    if trimmed.endswith("|"):
        trimmed = trimmed[:-1]
    return [cell.strip() for cell in trimmed.split("|")]


def _linkify_escaped_urls(text: str) -> str:
    """Wrap bare http(s) URLs in anchors after HTML escaping."""

    def replace(match: re.Match[str]) -> str:
        url = match.group("url")
        return f'<a href="{html_escape(url, quote=True)}">{url}</a>'

    return _INLINE_URL_RE.sub(replace, text)


def _render_inline_markdown(text: str, *, image_html: dict[str, str]) -> str:
    """Render a small inline Markdown subset into HTML."""
    rendered = html_escape(text)
    placeholders: dict[str, str] = {}

    def stash(fragment: str) -> str:
        token = f"__CHAT_AGENT_INLINE_{len(placeholders)}__"
        placeholders[token] = fragment
        return token

    rendered = re.sub(
        r"`([^`]+)`",
        lambda match: stash(f"<code>{match.group(1)}</code>"),
        rendered,
    )
    rendered = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        lambda match: (
            stash(
                f'<a href="{html_escape(match.group(2), quote=True)}">'
                f"{match.group(1)}</a>"
            )
        ),
        rendered,
    )
    rendered = _linkify_escaped_urls(rendered)
    rendered = re.sub(
        r"\*\*([^*]+)\*\*",
        lambda match: f"<strong>{match.group(1)}</strong>",
        rendered,
    )
    rendered = re.sub(
        r"(?<!\*)\*([^*]+)\*(?!\*)",
        lambda match: f"<em>{match.group(1)}</em>",
        rendered,
    )
    for token, html in image_html.items():
        rendered = rendered.replace(token, html)
    for token, fragment in placeholders.items():
        rendered = rendered.replace(token, fragment)
    return rendered


def _render_markdown_subset_to_html(
    markdown_text: str,
    *,
    image_html: dict[str, str],
) -> str:
    """Render the supported Markdown subset into HTML."""
    lines = markdown_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    i = 0
    pending_blank_line = False

    def append_block(block: str) -> None:
        nonlocal pending_blank_line
        if pending_blank_line and blocks and blocks[-1] != "<div><br></div>":
            blocks.append("<div><br></div>")
        pending_blank_line = False
        blocks.append(block)

    def render_heading(level: int, text: str) -> str:
        body = _render_inline_markdown(text, image_html=image_html)
        if level == 1:
            return (
                '<div><h1 style="font-size: 15.0pt; font-weight: bold;">'
                f"{body}</h1></div>"
            )
        if level == 2:
            return (
                '<div><h2 style="font-size: 13.5pt; font-weight: bold;">'
                f"{body}</h2></div>"
            )
        return (
            '<div><h3 style="font-size: 12pt; font-weight: bold;">'
            f"{body}</h3></div>"
        )

    def flush_paragraph(paragraph_lines: list[str]) -> None:
        if not paragraph_lines:
            return
        body = "<br>".join(
            _render_inline_markdown(line, image_html=image_html)
            for line in paragraph_lines
        )
        append_block(f"<div>{body}</div>")

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            pending_blank_line = True
            i += 1
            continue

        if stripped.startswith("```"):
            fence_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                fence_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            append_block(
                f"<pre><code>{html_escape('\n'.join(fence_lines))}</code></pre>"
            )
            continue

        if _TABLE_SEPARATOR_RE.match(lines[i + 1].strip()) if i + 1 < len(lines) else False:
            table_lines = [line]
            i += 2
            while i < len(lines) and "|" in lines[i]:
                candidate = lines[i].strip()
                if not candidate:
                    break
                if candidate.startswith("#") or candidate.startswith("```"):
                    break
                table_lines.append(lines[i])
                i += 1
            header_cells = _split_table_row(table_lines[0])
            body_rows = [_split_table_row(row) for row in table_lines[1:]]
            table_parts = ["<table><thead><tr>"]
            for cell in header_cells:
                table_parts.append(
                    f"<th>{_render_inline_markdown(cell, image_html=image_html)}</th>"
                )
            table_parts.append("</tr></thead><tbody>")
            for row in body_rows:
                table_parts.append("<tr>")
                for cell in row:
                    table_parts.append(
                        f"<td>{_render_inline_markdown(cell, image_html=image_html)}</td>"
                    )
                table_parts.append("</tr>")
            table_parts.append("</tbody></table>")
            append_block("".join(table_parts))
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            append_block(render_heading(level, heading_match.group(2)))
            i += 1
            continue

        if re.match(r"^\s*[-*]\s+.+$", line):
            items: list[str] = []
            while i < len(lines):
                match = re.match(r"^\s*[-*]\s+(.+)$", lines[i])
                if not match:
                    break
                items.append(
                    f"<div>- {_render_inline_markdown(match.group(1), image_html=image_html)}</div>"
                )
                i += 1
            for item in items:
                append_block(item)
            continue

        if _ORDERED_LIST_RE.match(line):
            items: list[str] = []
            number = 1
            while i < len(lines):
                match = _ORDERED_LIST_RE.match(lines[i])
                if not match:
                    break
                items.append(
                    f"<div>{number}. {_render_inline_markdown(match.group('body'), image_html=image_html)}</div>"
                )
                number += 1
                i += 1
            for item in items:
                append_block(item)
            continue

        if re.match(r"^\s*>\s*.+$", line):
            items: list[str] = []
            while i < len(lines):
                match = re.match(r"^\s*>\s*(.+)$", lines[i])
                if not match:
                    break
                items.append(
                    "<div><font color=\"#666666\">&gt; "
                    + _render_inline_markdown(match.group(1), image_html=image_html)
                    + "</font></div>"
                )
                i += 1
            for item in items:
                append_block(item)
            continue

        paragraph_lines = [line]
        i += 1
        while i < len(lines):
            candidate = lines[i]
            candidate_stripped = candidate.strip()
            if not candidate_stripped:
                break
            if candidate_stripped.startswith("```"):
                break
            if re.match(r"^(#{1,3})\s+.+$", candidate_stripped):
                break
            if re.match(r"^\s*[-*]\s+.+$", candidate):
                break
            if _ORDERED_LIST_RE.match(candidate):
                break
            if i + 1 < len(lines) and _TABLE_SEPARATOR_RE.match(lines[i + 1].strip()):
                break
            paragraph_lines.append(candidate)
            i += 1
        flush_paragraph(paragraph_lines)

    return "".join(blocks) or "<div><br></div>"


def _read_template_image_data(
    *,
    image_key: str,
    image_path: str,
    allowed_paths: list[str],
    base_dir: Path,
    alt_text: str,
) -> str:
    """Load one template image and return an HTML img tag."""
    expanded = str(Path(image_path).expanduser())
    if not is_path_allowed(expanded, allowed_paths, base_dir):
        raise ValueError(f"images.{image_key} is outside allowed paths: {image_path}")
    target = Path(expanded)
    if not target.is_absolute():
        target = (base_dir / target).resolve()
    else:
        target = target.resolve()
    if not target.exists():
        raise FileNotFoundError(f"image not found: {image_path}")
    media_type = _NOTE_IMAGE_EXTENSIONS.get(target.suffix.lower())
    if media_type is None:
        raise ValueError(
            f"unsupported image format for {image_key}: {target.suffix.lower()}"
        )
    payload = base64.b64encode(target.read_bytes()).decode("ascii")
    escaped_alt = html_escape(alt_text or image_key, quote=True)
    return (
        f'<img src="data:{media_type};base64,{payload}" alt="{escaped_alt}">'
    )


def _render_note_template_html(
    *,
    template_markdown: str,
    variables: dict[str, str],
    images: dict[str, str],
    allowed_paths: list[str],
    base_dir: Path,
) -> str:
    """Render a Markdown template plus variables/images into Notes HTML."""
    image_tokens: dict[str, str] = {}
    image_counter = 0

    def allocate_image_token(image_key: str, alt_text: str) -> str:
        nonlocal image_counter
        if image_key not in images:
            raise ValueError(f"template references unknown image placeholder: {image_key}")
        token = f"__CHAT_AGENT_IMAGE_{image_counter}__"
        image_counter += 1
        image_tokens[token] = _read_template_image_data(
            image_key=image_key,
            image_path=images[image_key],
            allowed_paths=allowed_paths,
            base_dir=base_dir,
            alt_text=alt_text,
        )
        return token

    template_with_markdown_images = _MARKDOWN_IMAGE_RE.sub(
        lambda match: allocate_image_token(
            match.group("ref"),
            match.group("alt"),
        ),
        template_markdown,
    )

    def replace_template_var(match: re.Match[str]) -> str:
        name = match.group("name")
        if name in variables:
            return variables[name]
        if name in images:
            return allocate_image_token(name, name)
        raise ValueError(f"template references unknown placeholder: {name}")

    rendered_markdown = _TEMPLATE_VAR_RE.sub(
        replace_template_var,
        template_with_markdown_images,
    )
    return _render_markdown_subset_to_html(
        rendered_markdown,
        image_html=image_tokens,
    )


def _ensure_note_title_html(note_html: str, title: str | None) -> str:
    """Guarantee that Notes sees the requested title as the first visible line."""
    if not title:
        return note_html
    first_line = _first_visible_markdown_line(_html_to_markdown(note_html))
    if first_line == title.strip():
        return note_html
    return (
        '<div><h1 style="font-size: 15.0pt; font-weight: bold;">'
        f"{html_escape(title)}</h1></div><div><br></div>{note_html}"
    )


def _apple_notes_cache_filename(note_id: str) -> str:
    """Build a stable cache filename for a note id."""
    digest = hashlib.sha256(note_id.encode("utf-8")).hexdigest()
    return f"{digest}.json"


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    """Atomically persist JSON data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _load_json_file(path: Path) -> dict[str, Any] | None:
    """Load JSON from disk when present and valid."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _coerce_note_content_kind(*, source_url: str | None, has_images: bool) -> str:
    """Classify the rendered note content for the LLM."""
    if source_url and has_images:
        return "web_clip_image"
    if source_url:
        return "web_clip_text"
    if has_images:
        return "mixed_note"
    return "plain_note"


def _applescript_utf8_file_read(name: str) -> str:
    """Return AppleScript that reads a UTF-8 temp file for the given variable."""
    return f'(read (POSIX file (system attribute "{name}_FILE")) as «class utf8»)'


def _format_app_tool_log_details(details: dict[str, Any] | None) -> str:
    """Render a privacy-aware summary for Apple app tool diagnostics."""
    if not details:
        return "-"
    safe_pairs: list[str] = []
    for key, value in details.items():
        if value in (None, "", [], {}):
            continue
        if key in {
            "account",
            "calendar",
            "folder_path",
            "folder_id",
            "target_folder_path",
            "target_folder_id",
            "list_name",
            "list_path",
            "list_id",
            "album_name",
            "album_path",
            "album_id",
            "parent_folder_path",
            "parent_folder_id",
            "event_uid",
            "exclude_event_uid",
            "reminder_id",
            "note_id",
            "sort_by",
            "limit",
            "start",
            "end",
            "due",
            "due_start",
            "due_end",
            "favorite",
            "all_day",
            "completed",
            "flagged",
        }:
            safe_pairs.append(f"{key}={value!r}")
            continue
        if isinstance(value, str):
            safe_pairs.append(f"{key}_chars={len(value)}")
            continue
        if isinstance(value, list):
            safe_pairs.append(f"{key}_count={len(value)}")
            continue
        safe_pairs.append(f"{key}={value!r}")
    return ", ".join(safe_pairs) if safe_pairs else "-"


class MacOSAppBridge:
    """Bridge for macOS personal apps using JXA/AppleScript."""

    def __init__(
        self,
        *,
        base_dir: Path,
        allowed_paths: list[str],
        timeout_seconds: float,
        max_search_results: int,
        photos_export_dir: str,
        vision_agent: Any | None = None,
        notes_summarizer: Any | None = None,
    ) -> None:
        self._base_dir = base_dir
        self._allowed_paths = allowed_paths
        self._timeout_seconds = timeout_seconds
        self._max_search_results = max_search_results
        self._photos_export_dir = photos_export_dir
        self._vision_agent = vision_agent
        self._notes_summarizer = notes_summarizer
        self._apple_notes_cache_dir = self._base_dir / "cache" / "apple_notes"
        self._apple_notes_cache_dir.mkdir(parents=True, exist_ok=True)

    def calendar_catalog(self) -> dict[str, Any]:
        """List calendars."""
        script = """
const app = Application("Calendar");
const calendars = app.calendars().map((cal) => ({
  name: cal.name(),
  writable: !!cal.writable(),
  description: valueOrNull(cal.description()),
  color: valueOrNull(cal.color()),
}));
return { ok: true, calendars, count: calendars.length };
"""
        return self._run_jxa_json(script)

    def calendar_search(
        self,
        *,
        calendar: str | None,
        calendars: list[str] | None,
        query: str | None,
        start: str | None,
        end: str | None,
        all_day: bool | None,
        sort_by: str | None,
        limit: int | None,
    ) -> dict[str, Any]:
        """Search calendar events."""
        script = f"""
const app = Application("Calendar");
const payload = readPayload();
const limit = clampLimit(payload.limit, {self._max_search_results});
const query = lower(payload.query || "");
const start = payload.start ? new Date(payload.start) : null;
const end = payload.end ? new Date(payload.end) : null;
let calendars = [];
if (payload.calendars && payload.calendars.length > 0) {{
  calendars = payload.calendars.map((name) => app.calendars.byName(name));
}} else if (payload.calendar) {{
  calendars = [app.calendars.byName(payload.calendar)];
}} else {{
  calendars = app.calendars();
}}
const results = [];
for (const cal of calendars) {{
  if (!cal.exists()) {{
    return {{ ok: false, error: `calendar not found: ${{payload.calendar || payload.calendars[0]}}` }};
  }}
  for (const event of cal.events()) {{
    const row = {{
      uid: event.uid(),
      title: event.summary(),
      start: iso(event.startDate()),
      end: iso(event.endDate()),
      location: valueOrNull(event.location()),
      notes: valueOrNull(event.description()),
      calendar: cal.name(),
      all_day: !!event.alldayEvent(),
      url: valueOrNull(event.url()),
    }};
    if (payload.all_day !== null && payload.all_day !== undefined && row.all_day !== payload.all_day) {{
      continue;
    }}
    if (start && row.end && new Date(row.end) < start) {{
      continue;
    }}
    if (end && row.start && new Date(row.start) > end) {{
      continue;
    }}
    const haystack = lower(`${{row.title || ""}}\\n${{row.location || ""}}\\n${{row.notes || ""}}`);
    if (query && !haystack.includes(query)) {{
      continue;
    }}
    results.push(row);
    if (results.length >= limit) {{
      break;
    }}
  }}
  if (results.length >= limit) {{
    break;
  }}
}}
if (payload.sort_by === "start_desc") {{
  results.sort((a, b) => compareIsoDesc(a.start, b.start));
}} else {{
  results.sort((a, b) => compareIsoAsc(a.start, b.start));
}}
return {{ ok: true, results, count: results.length }};
"""
        return self._run_jxa_json(
            script,
            payload={
                "calendar": calendar,
                "calendars": calendars,
                "query": query,
                "start": start,
                "end": end,
                "all_day": all_day,
                "sort_by": sort_by,
                "limit": limit,
            },
        )

    def calendar_conflicts(
        self,
        *,
        calendar: str | None,
        calendars: list[str] | None,
        start: str,
        end: str,
        exclude_event_uid: str | None,
        all_day: bool | None,
        limit: int | None,
    ) -> dict[str, Any]:
        """Find calendar events overlapping a candidate time range."""
        script = f"""
const app = Application("Calendar");
const payload = readPayload();
const start = new Date(payload.start);
const end = new Date(payload.end);
const limit = clampLimit(payload.limit, {self._max_search_results});
let calendars = [];
if (payload.calendars && payload.calendars.length > 0) {{
  calendars = payload.calendars.map((name) => app.calendars.byName(name));
}} else if (payload.calendar) {{
  calendars = [app.calendars.byName(payload.calendar)];
}} else {{
  calendars = app.calendars();
}}
const results = [];
for (const cal of calendars) {{
  if (!cal.exists()) {{
    return {{ ok: false, error: `calendar not found: ${{payload.calendar || payload.calendars[0]}}` }};
  }}
  const matches = cal.events.whose({{ startDate: {{ "<": end }}, endDate: {{ ">": start }} }})();
  for (const event of matches) {{
    const row = {{
      uid: event.uid(),
      title: event.summary(),
      start: iso(event.startDate()),
      end: iso(event.endDate()),
      location: valueOrNull(event.location()),
      notes: valueOrNull(event.description()),
      calendar: cal.name(),
      all_day: !!event.alldayEvent(),
      url: valueOrNull(event.url()),
    }};
    if (payload.exclude_event_uid && row.uid === payload.exclude_event_uid) {{
      continue;
    }}
    if (payload.all_day !== null && payload.all_day !== undefined && row.all_day !== payload.all_day) {{
      continue;
    }}
    results.push(row);
    if (results.length >= limit) {{
      break;
    }}
  }}
  if (results.length >= limit) {{
    break;
  }}
}}
results.sort((a, b) => compareIsoAsc(a.start, b.start));
return {{
  ok: true,
  requested_range: {{ start: payload.start, end: payload.end }},
  conflicts: results,
  count: results.length,
}};
"""
        return self._run_jxa_json(
            script,
            payload={
                "calendar": calendar,
                "calendars": calendars,
                "start": start,
                "end": end,
                "exclude_event_uid": exclude_event_uid,
                "all_day": all_day,
                "limit": limit,
            },
        )

    def calendar_get(
        self,
        *,
        event_uid: str,
        calendar: str | None = None,
    ) -> dict[str, Any]:
        """Fetch one calendar event by uid."""
        result = self._run_jxa_json(
            f"""
const app = Application("Calendar");
const payload = readPayload();
const calendars = payload.calendar ? [app.calendars.byName(payload.calendar)] : app.calendars();
for (const cal of calendars) {{
  if (!cal.exists()) {{
    continue;
  }}
  const matches = cal.events.whose({{ uid: payload.event_uid }})();
  if (matches.length > 0) {{
    const event = matches[0];
    return {{
      ok: true,
      event: {{
        uid: event.uid(),
        title: event.summary(),
        start: iso(event.startDate()),
        end: iso(event.endDate()),
        location: valueOrNull(event.location()),
        notes: valueOrNull(event.description()),
        calendar: cal.name(),
        all_day: !!event.alldayEvent(),
        url: valueOrNull(event.url()),
      }},
    }};
  }}
}}
return {{ ok: false, error: `event not found: ${{payload.event_uid}}` }};
""",
            payload={"event_uid": event_uid, "calendar": calendar},
        )
        return result

    def calendar_create(
        self,
        *,
        calendar: str,
        title: str,
        start: datetime,
        end: datetime,
        notes: str | None,
        location: str | None,
        url: str | None,
        all_day: bool | None,
    ) -> dict[str, Any]:
        """Create a calendar event."""
        env = {
            "EVENT_ALL_DAY": "1" if all_day else "0",
            **_datetime_env("START", start),
            **_datetime_env("END", end),
        }
        script = f"""
set calendarName to {_applescript_utf8_file_read("CALENDAR_NAME")}
set eventTitle to {_applescript_utf8_file_read("EVENT_TITLE")}
set eventNotes to {_applescript_utf8_file_read("EVENT_NOTES")}
set eventLocation to {_applescript_utf8_file_read("EVENT_LOCATION")}
set eventURL to {_applescript_utf8_file_read("EVENT_URL")}
set isAllDay to (system attribute "EVENT_ALL_DAY") is "1"
set startDate to current date
set year of startDate to ((system attribute "START_YEAR") as integer)
set month of startDate to ((system attribute "START_MONTH") as integer)
set day of startDate to ((system attribute "START_DAY") as integer)
set hours of startDate to ((system attribute "START_HOUR") as integer)
set minutes of startDate to ((system attribute "START_MINUTE") as integer)
set seconds of startDate to ((system attribute "START_SECOND") as integer)
set endDate to current date
set year of endDate to ((system attribute "END_YEAR") as integer)
set month of endDate to ((system attribute "END_MONTH") as integer)
set day of endDate to ((system attribute "END_DAY") as integer)
set hours of endDate to ((system attribute "END_HOUR") as integer)
set minutes of endDate to ((system attribute "END_MINUTE") as integer)
set seconds of endDate to ((system attribute "END_SECOND") as integer)
tell application "Calendar"
  tell calendar calendarName
    set newEvent to make new event with properties {{summary:eventTitle, start date:startDate, end date:endDate}}
    if eventNotes is not "" then set description of newEvent to eventNotes
    if eventLocation is not "" then set location of newEvent to eventLocation
    if eventURL is not "" then set url of newEvent to eventURL
    set allday event of newEvent to isAllDay
    return uid of newEvent
  end tell
end tell
"""
        uid = self._run_applescript(
            script,
            env=env,
            utf8_files={
                "CALENDAR_NAME": calendar,
                "EVENT_TITLE": title,
                "EVENT_NOTES": notes or "",
                "EVENT_LOCATION": location or "",
                "EVENT_URL": url or "",
            },
        )
        return self.calendar_get(event_uid=uid, calendar=calendar)

    def calendar_update(
        self,
        *,
        event_uid: str,
        calendar: str | None,
        title: str | None,
        start: datetime | None,
        end: datetime | None,
        notes: str | None,
        location: str | None,
        url: str | None,
        all_day: bool | None,
    ) -> dict[str, Any]:
        """Update a calendar event."""
        target = self.calendar_get(event_uid=event_uid, calendar=calendar)
        if not target.get("ok"):
            return target
        target_calendar = target["event"]["calendar"]
        env = {
            "EVENT_UID": event_uid,
            "HAS_TITLE": "1" if title is not None else "0",
            "HAS_NOTES": "1" if notes is not None else "0",
            "HAS_LOCATION": "1" if location is not None else "0",
            "HAS_URL": "1" if url is not None else "0",
            "HAS_ALL_DAY": "1" if all_day is not None else "0",
            "EVENT_ALL_DAY": "1" if all_day else "0",
            "HAS_START": "1" if start is not None else "0",
            "HAS_END": "1" if end is not None else "0",
        }
        if start is not None:
            env.update(_datetime_env("START", start))
        if end is not None:
            env.update(_datetime_env("END", end))
        script = f"""
set eventUID to system attribute "EVENT_UID"
set calendarName to {_applescript_utf8_file_read("CALENDAR_NAME")}
tell application "Calendar"
  tell calendar calendarName
    set targetEvent to first event whose uid is eventUID
    if (system attribute "HAS_TITLE") is "1" then set summary of targetEvent to {_applescript_utf8_file_read("EVENT_TITLE")}
    if (system attribute "HAS_NOTES") is "1" then set description of targetEvent to {_applescript_utf8_file_read("EVENT_NOTES")}
    if (system attribute "HAS_LOCATION") is "1" then set location of targetEvent to {_applescript_utf8_file_read("EVENT_LOCATION")}
    if (system attribute "HAS_URL") is "1" then set url of targetEvent to {_applescript_utf8_file_read("EVENT_URL")}
    if (system attribute "HAS_ALL_DAY") is "1" then set allday event of targetEvent to ((system attribute "EVENT_ALL_DAY") is "1")
    if (system attribute "HAS_START") is "1" then
      set startDate to current date
      set year of startDate to ((system attribute "START_YEAR") as integer)
      set month of startDate to ((system attribute "START_MONTH") as integer)
      set day of startDate to ((system attribute "START_DAY") as integer)
      set hours of startDate to ((system attribute "START_HOUR") as integer)
      set minutes of startDate to ((system attribute "START_MINUTE") as integer)
      set seconds of startDate to ((system attribute "START_SECOND") as integer)
      set start date of targetEvent to startDate
    end if
    if (system attribute "HAS_END") is "1" then
      set endDate to current date
      set year of endDate to ((system attribute "END_YEAR") as integer)
      set month of endDate to ((system attribute "END_MONTH") as integer)
      set day of endDate to ((system attribute "END_DAY") as integer)
      set hours of endDate to ((system attribute "END_HOUR") as integer)
      set minutes of endDate to ((system attribute "END_MINUTE") as integer)
      set seconds of endDate to ((system attribute "END_SECOND") as integer)
      set end date of targetEvent to endDate
    end if
    return uid of targetEvent
  end tell
end tell
"""
        uid = self._run_applescript(
            script,
            env=env,
            utf8_files={
                "CALENDAR_NAME": target_calendar,
                "EVENT_TITLE": title or "",
                "EVENT_NOTES": notes or "",
                "EVENT_LOCATION": location or "",
                "EVENT_URL": url or "",
            },
        )
        return self.calendar_get(event_uid=uid, calendar=target_calendar)

    def reminders_catalog(self) -> dict[str, Any]:
        """List reminder accounts and lists."""
        script = """
const app = Application("Reminders");
const accounts = app.accounts().map((account) => ({
  id: account.id(),
  name: account.name(),
  lists: account.lists().map((list) => ({
    id: list.id(),
    name: list.name(),
    account: account.name(),
  })),
}));
return { ok: true, accounts, count: accounts.reduce((n, account) => n + account.lists.length, 0) };
"""
        return self._run_jxa_json(script)

    def reminders_search(
        self,
        *,
        list_id: str | None,
        list_name: str | None,
        list_path: str | None,
        query: str | None,
        due_start: str | None,
        due_end: str | None,
        completed: bool | None,
        flagged: bool | None,
        priority_min: int | None,
        priority_max: int | None,
        sort_by: str | None,
        limit: int | None,
    ) -> dict[str, Any]:
        """Search reminders."""
        script = f"""
const app = Application("Reminders");
const payload = readPayload();
const limit = clampLimit(payload.limit, {self._max_search_results});
const query = lower(payload.query || "");
const dueStart = payload.due_start ? new Date(payload.due_start) : null;
const dueEnd = payload.due_end ? new Date(payload.due_end) : null;
let lists = [];
if (payload.list_id) {{
  lists = app.lists.whose({{ id: payload.list_id }})();
}} else if (payload.list_path) {{
  for (const account of app.accounts()) {{
    for (const list of account.lists()) {{
      const path = `${{account.name()}}/${{list.name()}}`;
      if (path === payload.list_path) {{
        lists = [list];
        break;
      }}
    }}
    if (lists.length > 0) {{
      break;
    }}
  }}
}} else if (payload.list_name) {{
  lists = [app.lists.byName(payload.list_name)];
}} else {{
  lists = app.lists();
}}
const results = [];
for (const list of lists) {{
  if (!list.exists()) {{
    continue;
  }}
  for (const reminder of list.reminders()) {{
    const row = {{
      id: reminder.id(),
      title: reminder.name(),
      notes: valueOrNull(reminder.body()),
      completed: !!reminder.completed(),
      due: iso(reminder.dueDate()),
      priority: reminder.priority(),
      flagged: !!reminder.flagged(),
      list_id: list.id(),
      list_name: list.name(),
      list_path: `${{list.container().name()}}/${{list.name()}}`,
    }};
    if (payload.completed !== null && payload.completed !== undefined && row.completed !== payload.completed) {{
      continue;
    }}
    if (payload.flagged !== null && payload.flagged !== undefined && row.flagged !== payload.flagged) {{
      continue;
    }}
    if (payload.priority_min !== null && payload.priority_min !== undefined && row.priority < payload.priority_min) {{
      continue;
    }}
    if (payload.priority_max !== null && payload.priority_max !== undefined && row.priority > payload.priority_max) {{
      continue;
    }}
    if (dueStart && (!row.due || new Date(row.due) < dueStart)) {{
      continue;
    }}
    if (dueEnd && (!row.due || new Date(row.due) > dueEnd)) {{
      continue;
    }}
    const haystack = lower(`${{row.title || ""}}\\n${{row.notes || ""}}`);
    if (query && !haystack.includes(query)) {{
      continue;
    }}
    results.push(row);
    if (results.length >= limit) {{
      break;
    }}
  }}
  if (results.length >= limit) {{
    break;
  }}
}}
if (payload.sort_by === "due_desc") {{
  results.sort((a, b) => compareIsoDesc(a.due, b.due));
}} else if (payload.sort_by === "title_asc") {{
  results.sort((a, b) => compareTextAsc(a.title, b.title));
}} else {{
  results.sort((a, b) => compareIsoAsc(a.due, b.due));
}}
return {{ ok: true, results, count: results.length }};
"""
        return self._run_jxa_json(
            script,
            payload={
                "list_id": list_id,
                "list_name": list_name,
                "list_path": list_path,
                "query": query,
                "due_start": due_start,
                "due_end": due_end,
                "completed": completed,
                "flagged": flagged,
                "priority_min": priority_min,
                "priority_max": priority_max,
                "sort_by": sort_by,
                "limit": limit,
            },
        )

    def reminders_get(self, *, reminder_id: str) -> dict[str, Any]:
        """Fetch one reminder by id."""
        return self._run_jxa_json(
            """
const app = Application("Reminders");
const payload = readPayload();
const matches = app.reminders.whose({ id: payload.reminder_id })();
if (matches.length === 0) {
  return { ok: false, error: `reminder not found: ${payload.reminder_id}` };
}
const reminder = matches[0];
const list = reminder.container();
return {
  ok: true,
  reminder: {
    id: reminder.id(),
    title: reminder.name(),
    notes: valueOrNull(reminder.body()),
    completed: !!reminder.completed(),
    due: iso(reminder.dueDate()),
    priority: reminder.priority(),
      flagged: !!reminder.flagged(),
      list_id: list.id(),
      list_name: list.name(),
      list_path: `${list.container().name()}/${list.name()}`,
    },
};
""",
            payload={"reminder_id": reminder_id},
        )

    def reminders_create(
        self,
        *,
        list_id: str | None,
        list_name: str | None,
        list_path: str | None,
        title: str,
        notes: str | None,
        due: datetime | None,
        priority: int | None,
        flagged: bool | None,
    ) -> dict[str, Any]:
        """Create a reminder."""
        resolved = self._resolve_list_spec(
            list_id=list_id,
            list_name=list_name,
            list_path=list_path,
        )
        if not resolved.get("ok"):
            return resolved
        env = {
            "HAS_DUE": "1" if due is not None else "0",
            "HAS_PRIORITY": "1" if priority is not None else "0",
            "PRIORITY": str(priority or 0),
            "HAS_FLAGGED": "1" if flagged is not None else "0",
            "FLAGGED": "1" if flagged else "0",
        }
        if due is not None:
            env.update(_datetime_env("DUE", due))
        script = f"""
set listName to {_applescript_utf8_file_read("LIST_NAME")}
set reminderTitle to {_applescript_utf8_file_read("TITLE")}
tell application "Reminders"
  tell list listName
    set newReminder to make new reminder with properties {{name:reminderTitle}}
    if {_applescript_utf8_file_read("NOTES")} is not "" then set body of newReminder to {_applescript_utf8_file_read("NOTES")}
    if (system attribute "HAS_PRIORITY") is "1" then set priority of newReminder to ((system attribute "PRIORITY") as integer)
    if (system attribute "HAS_FLAGGED") is "1" then set flagged of newReminder to ((system attribute "FLAGGED") is "1")
    if (system attribute "HAS_DUE") is "1" then
      set dueDate to current date
      set year of dueDate to ((system attribute "DUE_YEAR") as integer)
      set month of dueDate to ((system attribute "DUE_MONTH") as integer)
      set day of dueDate to ((system attribute "DUE_DAY") as integer)
      set hours of dueDate to ((system attribute "DUE_HOUR") as integer)
      set minutes of dueDate to ((system attribute "DUE_MINUTE") as integer)
      set seconds of dueDate to ((system attribute "DUE_SECOND") as integer)
      set due date of newReminder to dueDate
    end if
    return id of newReminder
  end tell
end tell
"""
        reminder_id = self._run_applescript(
            script,
            env=env,
            utf8_files={
                "LIST_NAME": resolved["list_name"],
                "TITLE": title,
                "NOTES": notes or "",
            },
        )
        return self.reminders_get(reminder_id=reminder_id)

    def reminders_update(
        self,
        *,
        reminder_id: str,
        title: str | None,
        notes: str | None,
        due: datetime | None,
        priority: int | None,
        flagged: bool | None,
        completed: bool | None,
    ) -> dict[str, Any]:
        """Update a reminder."""
        env = {
            "REMINDER_ID": reminder_id,
            "HAS_TITLE": "1" if title is not None else "0",
            "HAS_NOTES": "1" if notes is not None else "0",
            "HAS_DUE": "1" if due is not None else "0",
            "HAS_PRIORITY": "1" if priority is not None else "0",
            "PRIORITY": str(priority or 0),
            "HAS_FLAGGED": "1" if flagged is not None else "0",
            "FLAGGED": "1" if flagged else "0",
            "HAS_COMPLETED": "1" if completed is not None else "0",
            "COMPLETED": "1" if completed else "0",
        }
        if due is not None:
            env.update(_datetime_env("DUE", due))
        script = f"""
set reminderId to system attribute "REMINDER_ID"
tell application "Reminders"
  set targetReminder to first reminder whose id is reminderId
  if (system attribute "HAS_TITLE") is "1" then set name of targetReminder to {_applescript_utf8_file_read("TITLE")}
  if (system attribute "HAS_NOTES") is "1" then set body of targetReminder to {_applescript_utf8_file_read("NOTES")}
  if (system attribute "HAS_PRIORITY") is "1" then set priority of targetReminder to ((system attribute "PRIORITY") as integer)
  if (system attribute "HAS_FLAGGED") is "1" then set flagged of targetReminder to ((system attribute "FLAGGED") is "1")
  if (system attribute "HAS_COMPLETED") is "1" then set completed of targetReminder to ((system attribute "COMPLETED") is "1")
  if (system attribute "HAS_DUE") is "1" then
    set dueDate to current date
    set year of dueDate to ((system attribute "DUE_YEAR") as integer)
    set month of dueDate to ((system attribute "DUE_MONTH") as integer)
    set day of dueDate to ((system attribute "DUE_DAY") as integer)
    set hours of dueDate to ((system attribute "DUE_HOUR") as integer)
    set minutes of dueDate to ((system attribute "DUE_MINUTE") as integer)
    set seconds of dueDate to ((system attribute "DUE_SECOND") as integer)
    set due date of targetReminder to dueDate
  end if
  return id of targetReminder
end tell
"""
        updated_id = self._run_applescript(
            script,
            env=env,
            utf8_files={
                "TITLE": title or "",
                "NOTES": notes or "",
            },
        )
        return self.reminders_get(reminder_id=updated_id)

    def notes_catalog(self) -> dict[str, Any]:
        """List Notes accounts and folders."""
        script = """
const app = Application("Notes");
function walkFolder(folder, accountName) {
  const path = `${accountName}/${folder.name()}`;
  return {
    id: folder.id(),
    name: folder.name(),
    account: accountName,
    path,
    children: folder.folders().map((child) => walkChildFolder(child, path, accountName)),
  };
}
function walkChildFolder(folder, parentPath, accountName) {
  const path = `${parentPath}/${folder.name()}`;
  return {
    id: folder.id(),
    name: folder.name(),
    account: accountName,
    path,
    children: folder.folders().map((child) => walkChildFolder(child, path, accountName)),
  };
}
const accounts = app.accounts().map((account) => ({
  id: account.id(),
  name: account.name(),
  folders: account.folders().map((folder) => walkFolder(folder, account.name())),
}));
return { ok: true, accounts };
"""
        return self._run_jxa_json(
            script,
            operation="notes.catalog",
        )

    def _notes_list_candidates(
        self,
        *,
        account: str | None,
        folder_id: str | None,
        folder_path: str | None,
        created_after: str | None,
        created_before: str | None,
        modified_after: str | None,
        modified_before: str | None,
        sort_by: str | None,
    ) -> dict[str, Any]:
        """List note metadata without loading large note bodies."""
        script = f"""
const app = Application("Notes");
const payload = readPayload();
const scanLimit = clampLimit(payload.scan_limit, {self._max_search_results});
const createdAfter = payload.created_after ? new Date(payload.created_after) : null;
const createdBefore = payload.created_before ? new Date(payload.created_before) : null;
const modifiedAfter = payload.modified_after ? new Date(payload.modified_after) : null;
const modifiedBefore = payload.modified_before ? new Date(payload.modified_before) : null;
function flattenFolders(folder, accountName, parentPath) {{
  const path = parentPath ? `${{parentPath}}/${{folder.name()}}` : `${{accountName}}/${{folder.name()}}`;
  const entry = {{ id: folder.id(), name: folder.name(), account: accountName, path, notes: folder.notes() }};
  let rows = [entry];
  for (const child of folder.folders()) {{
    rows = rows.concat(flattenFolders(child, accountName, path));
  }}
  return rows;
}}
let folders = [];
for (const accountRow of app.accounts()) {{
  if (payload.account && accountRow.name() !== payload.account) {{
    continue;
  }}
  for (const folder of accountRow.folders()) {{
    folders = folders.concat(flattenFolders(folder, accountRow.name(), ""));
  }}
}}
if (payload.folder_id) {{
  folders = folders.filter((row) => row.id === payload.folder_id);
}}
if (payload.folder_path) {{
  folders = folders.filter((row) => row.path === payload.folder_path);
}}
const results = [];
for (const row of folders) {{
  for (const note of row.notes) {{
    const item = {{
      id: note.id(),
      title: note.name(),
      created_at: iso(note.creationDate()),
      modified_at: iso(note.modificationDate()),
      shared: !!note.shared(),
      password_protected: !!note.passwordProtected(),
      account: row.account,
      folder_id: row.id,
      folder_path: row.path,
    }};
    if (createdAfter && (!item.created_at || new Date(item.created_at) < createdAfter)) {{
      continue;
    }}
    if (createdBefore && (!item.created_at || new Date(item.created_at) > createdBefore)) {{
      continue;
    }}
    if (modifiedAfter && (!item.modified_at || new Date(item.modified_at) < modifiedAfter)) {{
      continue;
    }}
    if (modifiedBefore && (!item.modified_at || new Date(item.modified_at) > modifiedBefore)) {{
      continue;
    }}
    results.push(item);
    if (results.length >= scanLimit) {{
      break;
    }}
  }}
  if (results.length >= scanLimit) {{
    break;
  }}
}}
if (payload.sort_by === "modified_asc") {{
  results.sort((a, b) => compareIsoAsc(a.modified_at, b.modified_at));
}} else if (payload.sort_by === "created_desc") {{
  results.sort((a, b) => compareIsoDesc(a.created_at, b.created_at));
}} else if (payload.sort_by === "created_asc") {{
  results.sort((a, b) => compareIsoAsc(a.created_at, b.created_at));
}} else {{
  results.sort((a, b) => compareIsoDesc(a.modified_at, b.modified_at));
}}
return {{ ok: true, results, count: results.length }};
        """
        return self._run_jxa_json(
            script,
            payload={
                "account": account,
                "folder_id": folder_id,
                "folder_path": folder_path,
                "created_after": created_after,
                "created_before": created_before,
                "modified_after": modified_after,
                "modified_before": modified_before,
                "sort_by": sort_by,
                "scan_limit": self._max_search_results,
            },
            operation="notes.list_candidates",
            log_details={
                "account": account,
                "folder_id": folder_id,
                "folder_path": folder_path,
                "created_after": created_after,
                "created_before": created_before,
                "modified_after": modified_after,
                "modified_before": modified_before,
                "sort_by": sort_by,
                "scan_limit": self._max_search_results,
            },
        )

    def _notes_get_raw(self, *, note_id: str) -> dict[str, Any]:
        """Fetch one note by id with raw HTML/plaintext."""
        return self._run_jxa_json(
            """
const app = Application("Notes");
const payload = readPayload();
function buildFolderPath(folder) {
  const parts = [folder.name()];
  let container = null;
  try {
    container = folder.container();
  } catch (error) {
    container = null;
  }
  while (container) {
    try {
      parts.unshift(container.name());
      container = container.container();
    } catch (error) {
      break;
    }
  }
  return parts.join("/");
}
function resolveAccountName(folder) {
  let container = null;
  let accountName = null;
  try {
    container = folder.container();
  } catch (error) {
    container = null;
  }
  while (container) {
    try {
      accountName = container.name();
      container = container.container();
    } catch (error) {
      break;
    }
  }
  return accountName;
}
const matches = app.notes.whose({ id: payload.note_id })();
if (matches.length === 0) {
  return { ok: false, error: `note not found: ${payload.note_id}` };
}
const note = matches[0];
const folder = note.container();
return {
  ok: true,
  note: {
    id: note.id(),
    title: note.name(),
    body_html: valueOrNull(note.body()),
    plaintext: valueOrNull(note.plaintext()),
    created_at: iso(note.creationDate()),
    modified_at: iso(note.modificationDate()),
    shared: !!note.shared(),
    password_protected: !!note.passwordProtected(),
    account: resolveAccountName(folder),
    folder_id: folder.id(),
    folder_path: buildFolderPath(folder),
  },
};
""",
            payload={"note_id": note_id},
            operation="notes.get_raw",
            log_details={"note_id": note_id},
        )

    def _read_note_cache(self, *, note_id: str) -> dict[str, Any] | None:
        """Load the derived cache entry for one note."""
        return _load_json_file(
            self._apple_notes_cache_dir / _apple_notes_cache_filename(note_id)
        )

    def _write_note_cache(self, *, note_id: str, payload: dict[str, Any]) -> None:
        """Persist the derived cache entry for one note."""
        cache_payload = dict(payload)
        cache_payload["cache_version"] = _APPLE_NOTES_CACHE_VERSION
        _write_json_file(
            self._apple_notes_cache_dir / _apple_notes_cache_filename(note_id),
            cache_payload,
        )

    def _describe_embedded_image(
        self,
        *,
        image_bytes: bytes,
        media_type: str,
        image_index: int,
    ) -> str:
        """Describe one embedded note image with the shared vision agent."""
        if self._vision_agent is None:
            return f"Embedded image {image_index} omitted."
        try:
            description = self._vision_agent.describe(
                [
                    ContentPart(type="text", text=_APPLE_NOTES_IMAGE_PROMPT),
                    ContentPart(
                        type="image",
                        media_type=media_type,
                        data=base64.b64encode(image_bytes).decode("ascii"),
                    ),
                ]
            )
        except Exception as exc:
            logger.warning("apple-notes embedded image vision failed: %s", exc)
            return f"Embedded image {image_index} unavailable."
        return description.strip() or f"Embedded image {image_index}."

    def _render_note_markdown(
        self,
        *,
        note_id: str,
        body_html: str,
        plaintext: str,
    ) -> tuple[str, list[str], bool]:
        """Convert raw Notes HTML into Markdown and replace inline images with text."""
        image_hashes: list[str] = []
        image_counter = 0
        has_images = False

        def replace_data_image(match: re.Match[str]) -> str:
            nonlocal image_counter, has_images
            has_images = True
            image_counter += 1
            src = match.group("src")
            header, _, data_part = src.partition(",")
            media_type = header[5:].split(";", 1)[0] if header.startswith("data:") else "image/png"
            try:
                image_bytes = base64.b64decode(data_part, validate=False)
                image_hashes.append(hashlib.sha256(image_bytes).hexdigest())
            except Exception:
                return f"<p>[Embedded image {image_counter}]</p>"
            description = self._describe_embedded_image(
                image_bytes=image_bytes,
                media_type=media_type,
                image_index=image_counter,
            )
            escaped = html_escape(description).replace("\n", "<br>")
            return f"<p>[Embedded image {image_counter} summary]<br>{escaped}</p>"

        rendered_html = _DATA_IMAGE_RE.sub(replace_data_image, body_html or "")
        markdown = _normalize_markdown(_html_to_markdown(rendered_html)) if rendered_html else ""
        if not markdown:
            markdown = _normalize_markdown(plaintext or "")
        if not markdown:
            markdown = "(empty note)"
        logger.info(
            "apple-notes render note_id=%s has_images=%s image_count=%d markdown_chars=%d",
            note_id,
            has_images,
            len(image_hashes),
            len(markdown),
        )
        return markdown, image_hashes, has_images

    def _summarize_note_content(self, *, title: str | None, content_markdown: str) -> str:
        """Generate a short search summary for one note."""
        fallback = _normalize_markdown(content_markdown)[:280]
        if self._notes_summarizer is None:
            return fallback
        user_content = (
            f"標題：{title or '(untitled)'}\n"
            f"內容：\n{content_markdown[:_APPLE_NOTES_SUMMARY_MAX_INPUT_CHARS]}"
        )
        try:
            summary = self._notes_summarizer.chat(
                [
                    Message(role="system", content=_APPLE_NOTES_SUMMARY_SYSTEM_PROMPT),
                    Message(role="user", content=user_content),
                ]
            )
        except Exception as exc:
            logger.warning("apple-notes summary failed: %s", exc)
            return fallback
        normalized = _normalize_markdown(summary or "")
        return normalized or fallback

    def _build_note_view(
        self,
        raw_note: dict[str, Any],
        *,
        include_summary: bool,
    ) -> dict[str, Any]:
        """Build the LLM-facing note payload, using cache when possible."""
        note_id = raw_note["id"]
        modified_at = raw_note.get("modified_at")
        cached = self._read_note_cache(note_id=note_id)
        if (
            cached
            and cached.get("cache_version") == _APPLE_NOTES_CACHE_VERSION
            and cached.get("modified_at") == modified_at
        ):
            if include_summary and not cached.get("search_summary"):
                cached["search_summary"] = self._summarize_note_content(
                    title=cached.get("title"),
                    content_markdown=cached.get("content_markdown", ""),
                )
                self._write_note_cache(note_id=note_id, payload=cached)
            return cached

        content_markdown, image_hashes, has_images = self._render_note_markdown(
            note_id=note_id,
            body_html=raw_note.get("body_html") or "",
            plaintext=raw_note.get("plaintext") or "",
        )
        source_url = _extract_source_url(raw_note.get("body_html") or "")
        payload = {
            "id": note_id,
            "title": raw_note.get("title"),
            "created_at": raw_note.get("created_at"),
            "modified_at": modified_at,
            "shared": raw_note.get("shared", False),
            "password_protected": raw_note.get("password_protected", False),
            "account": raw_note.get("account"),
            "folder_id": raw_note.get("folder_id"),
            "folder_path": raw_note.get("folder_path"),
            "content_markdown": content_markdown,
            "content_chars": len(content_markdown),
            "has_images": has_images,
            "image_count": len(image_hashes),
            "image_hashes": image_hashes,
            "source_url": source_url,
            "content_kind": _coerce_note_content_kind(
                source_url=source_url,
                has_images=has_images,
            ),
            "search_summary": None,
        }
        if include_summary:
            payload["search_summary"] = self._summarize_note_content(
                title=payload.get("title"),
                content_markdown=content_markdown,
            )
        self._write_note_cache(note_id=note_id, payload=payload)
        return payload

    def _build_note_search_entry(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Render one note candidate into a cached search entry."""
        raw_result = self._notes_get_raw(note_id=candidate["id"])
        if not raw_result.get("ok"):
            raise RuntimeError(raw_result.get("error") or "failed to fetch note")
        return self._build_note_view(raw_result["note"], include_summary=True)

    def notes_search(
        self,
        *,
        account: str | None,
        folder_id: str | None,
        folder_path: str | None,
        query: str | None,
        created_after: str | None,
        created_before: str | None,
        modified_after: str | None,
        modified_before: str | None,
        sort_by: str | None,
        limit: int | None,
        offset: int | None,
    ) -> dict[str, Any]:
        """Search notes using rendered Markdown and cached summaries."""
        metadata = self._notes_list_candidates(
            account=account,
            folder_id=folder_id,
            folder_path=folder_path,
            created_after=created_after,
            created_before=created_before,
            modified_after=modified_after,
            modified_before=modified_before,
            sort_by=sort_by,
        )
        if not metadata.get("ok"):
            return metadata
        candidates = metadata.get("results", [])
        if not candidates:
            return {
                "ok": True,
                "results": [],
                "count": 0,
                "total_matches": 0,
                "offset": max(0, offset or 0),
                "limit": max(1, min(limit or _APPLE_NOTES_DEFAULT_SEARCH_LIMIT, self._max_search_results)),
                "has_more": False,
            }

        workers = min(_APPLE_NOTES_MAX_NOTE_WORKERS, len(candidates))
        if workers <= 1:
            rendered = [self._build_note_search_entry(candidate) for candidate in candidates]
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                rendered = list(executor.map(self._build_note_search_entry, candidates))

        needle = (query or "").strip().lower()
        if needle:
            rendered = [
                note
                for note in rendered
                if needle in (note.get("title") or "").lower()
                or needle in (note.get("search_summary") or "").lower()
                or needle in (note.get("content_markdown") or "").lower()
            ]

        if sort_by == "modified_asc":
            rendered.sort(key=lambda item: item.get("modified_at") or "")
        elif sort_by == "created_desc":
            rendered.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        elif sort_by == "created_asc":
            rendered.sort(key=lambda item: item.get("created_at") or "")
        else:
            rendered.sort(key=lambda item: item.get("modified_at") or "", reverse=True)

        safe_offset = max(0, offset or 0)
        safe_limit = max(
            1,
            min(limit or _APPLE_NOTES_DEFAULT_SEARCH_LIMIT, self._max_search_results),
        )
        page = rendered[safe_offset : safe_offset + safe_limit]
        results = [
            {
                "id": note["id"],
                "title": note.get("title"),
                "summary": note.get("search_summary") or "",
                "created_at": note.get("created_at"),
                "modified_at": note.get("modified_at"),
                "account": note.get("account"),
                "folder_id": note.get("folder_id"),
                "folder_path": note.get("folder_path"),
                "content_kind": note.get("content_kind"),
                "has_images": note.get("has_images", False),
                "image_count": note.get("image_count", 0),
                "source_url": note.get("source_url"),
                "content_chars": note.get("content_chars", 0),
            }
            for note in page
        ]
        logger.info(
            "apple-notes search folder=%s query_chars=%d scanned=%d matched=%d returned=%d offset=%d limit=%d",
            folder_path or folder_id or account or "*",
            len(query or ""),
            len(candidates),
            len(rendered),
            len(results),
            safe_offset,
            safe_limit,
        )
        return {
            "ok": True,
            "results": results,
            "count": len(results),
            "total_matches": len(rendered),
            "offset": safe_offset,
            "limit": safe_limit,
            "has_more": safe_offset + safe_limit < len(rendered),
        }

    def notes_get(self, *, note_id: str) -> dict[str, Any]:
        """Fetch one note by id and return rendered Markdown content."""
        raw_result = self._notes_get_raw(note_id=note_id)
        if not raw_result.get("ok"):
            return raw_result
        note = self._build_note_view(raw_result["note"], include_summary=False)
        return {
            "ok": True,
            "note": {
                "id": note["id"],
                "title": note.get("title"),
                "created_at": note.get("created_at"),
                "modified_at": note.get("modified_at"),
                "shared": note.get("shared", False),
                "password_protected": note.get("password_protected", False),
                "account": note.get("account"),
                "folder_id": note.get("folder_id"),
                "folder_path": note.get("folder_path"),
                "content_markdown": note.get("content_markdown", ""),
                "content_chars": note.get("content_chars", 0),
                "content_kind": note.get("content_kind"),
                "has_images": note.get("has_images", False),
                "image_count": note.get("image_count", 0),
                "source_url": note.get("source_url"),
            },
        }

    def notes_create(
        self,
        *,
        folder_id: str | None,
        folder_path: str | None,
        title: str | None,
        body: str | None,
        template_markdown: str | None,
        variables: dict[str, str] | None,
        images: dict[str, str] | None,
    ) -> dict[str, Any]:
        """Create a note."""
        target = self._resolve_note_folder(folder_id=folder_id, folder_path=folder_path)
        if not target.get("ok"):
            return target
        variables = dict(variables or {})
        if title is not None and "title" not in variables:
            variables["title"] = title
        if template_markdown is not None:
            note_body = _render_note_template_html(
                template_markdown=template_markdown,
                variables=variables,
                images=images or {},
                allowed_paths=self._allowed_paths,
                base_dir=self._base_dir,
            )
            note_body = _ensure_note_title_html(note_body, title)
        else:
            note_body = _build_note_html(title, body or "")
        env = {"FOLDER_ID": target["folder_id"]}
        script = f"""
set folderId to system attribute "FOLDER_ID"
set noteBody to {_applescript_utf8_file_read("NOTE_BODY")}
tell application "Notes"
  set targetFolder to first folder whose id is folderId
  tell targetFolder
    set newNote to make new note with properties {{body:noteBody}}
    return id of newNote
  end tell
end tell
"""
        note_id = self._run_applescript(
            script,
            env=env,
            utf8_files={"NOTE_BODY": note_body},
            operation="notes.create",
            log_details={
                "folder_id": target["folder_id"],
                "folder_path": target["folder_path"],
                "title": title or "",
                "body": body or "",
                "template_markdown": template_markdown or "",
                "variables": variables,
                "images": images or {},
            },
        )
        return self.notes_get(note_id=note_id)

    def notes_update(
        self,
        *,
        note_id: str,
        title: str | None,
        body: str | None,
        template_markdown: str | None,
        variables: dict[str, str] | None,
        images: dict[str, str] | None,
        append: bool,
    ) -> dict[str, Any]:
        """Update a note."""
        current = self._notes_get_raw(note_id=note_id)
        if not current.get("ok"):
            return current
        variables = dict(variables or {})
        if title is not None and "title" not in variables:
            variables["title"] = title
        if template_markdown is not None:
            payload = _render_note_template_html(
                template_markdown=template_markdown,
                variables=variables,
                images=images or {},
                allowed_paths=self._allowed_paths,
                base_dir=self._base_dir,
            )
            if not append:
                payload = _ensure_note_title_html(payload, title)
        else:
            payload = _build_note_html(title, body or "")
        body_html = current["note"]["body_html"] or ""
        if append:
            payload = body_html + payload
        env = {"NOTE_ID": note_id}
        script = f"""
set noteId to system attribute "NOTE_ID"
set noteBody to {_applescript_utf8_file_read("NOTE_BODY")}
tell application "Notes"
  set targetNote to first note whose id is noteId
  set body of targetNote to noteBody
  return id of targetNote
end tell
"""
        updated_id = self._run_applescript(
            script,
            env=env,
            utf8_files={"NOTE_BODY": payload},
            operation="notes.update",
            log_details={
                "note_id": note_id,
                "title": title or "",
                "body": body or "",
                "template_markdown": template_markdown or "",
                "variables": variables,
                "images": images or {},
                "append": append,
            },
        )
        return self.notes_get(note_id=updated_id)

    def notes_move(
        self,
        *,
        note_id: str,
        target_folder_id: str | None,
        target_folder_path: str | None,
    ) -> dict[str, Any]:
        """Move a note to another folder."""
        target = self._resolve_note_folder(
            folder_id=target_folder_id,
            folder_path=target_folder_path,
        )
        if not target.get("ok"):
            return target
        env = {
            "NOTE_ID": note_id,
            "TARGET_FOLDER_ID": target["folder_id"],
        }
        script = """
set noteId to system attribute "NOTE_ID"
set targetFolderId to system attribute "TARGET_FOLDER_ID"
tell application "Notes"
  set targetNote to first note whose id is noteId
  set targetFolder to first folder whose id is targetFolderId
  move targetNote to targetFolder
  return id of targetNote
end tell
"""
        moved_id = self._run_applescript(
            script,
            env=env,
            operation="notes.move",
            log_details={
                "note_id": note_id,
                "target_folder_id": target["folder_id"],
                "target_folder_path": target["folder_path"],
            },
        )
        return self.notes_get(note_id=moved_id)

    def photos_catalog(self) -> dict[str, Any]:
        """List Photos folders and albums."""
        script = """
const app = Application("Photos");
function walkFolder(folder, parentPath) {
  const path = parentPath ? `${parentPath}/${folder.name()}` : folder.name();
  return {
    id: folder.id(),
    name: folder.name(),
    path,
    albums: folder.albums().map((album) => ({
      id: album.id(),
      name: album.name(),
      path: `${path}/${album.name()}`,
      count: album.mediaItems().length,
    })),
    children: folder.folders().map((child) => walkFolder(child, path)),
  };
}
const rootFolders = app.folders().filter((folder) => !folder.parent());
const folders = rootFolders.map((folder) => walkFolder(folder, ""));
const topLevelAlbums = app.albums()
  .filter((album) => !album.parent())
  .map((album) => ({
    id: album.id(),
    name: album.name(),
    path: album.name(),
    count: album.mediaItems().length,
  }));
return { ok: true, folders, albums: topLevelAlbums };
"""
        return self._run_jxa_json(script)

    def photos_search(
        self,
        *,
        album_id: str | None,
        album_name: str | None,
        album_path: str | None,
        folder_id: str | None,
        folder_path: str | None,
        query: str | None,
        start: str | None,
        end: str | None,
        favorite: bool | None,
        sort_by: str | None,
        limit: int | None,
    ) -> dict[str, Any]:
        """Search the Photos library."""
        if (
            not any([album_id, album_name, album_path, folder_id, folder_path, query, start, end])
            and favorite is None
            and sort_by is not None
        ):
            return {
                "ok": False,
                "error": (
                    "sorting the entire Photos library requires a narrower scope; "
                    "provide album_path, folder_path, query, start/end, or favorite"
                ),
            }
        script = f"""
const app = Application("Photos");
const payload = readPayload();
const limit = clampLimit(payload.limit, {self._max_search_results});
const query = lower(payload.query || "");
const start = payload.start ? new Date(payload.start) : null;
const end = payload.end ? new Date(payload.end) : null;
function flattenFolder(folder, parentPath) {{
  const path = parentPath ? `${{parentPath}}/${{folder.name()}}` : folder.name();
  let rows = [{{
    id: folder.id(),
    name: folder.name(),
    path,
    albums: folder.albums().map((album) => ({{
      id: album.id(),
      name: album.name(),
      path: `${{path}}/${{album.name()}}`,
      album,
    }})),
  }}];
  for (const child of folder.folders()) {{
    rows = rows.concat(flattenFolder(child, path));
  }}
  return rows;
}}
let folderRows = [];
for (const folder of app.folders()) {{
  try {{
    if (!folder.parent()) {{
      folderRows = folderRows.concat(flattenFolder(folder, ""));
    }}
  }} catch (error) {{}}
}}
let scopeType = "library";
let scopeName = null;
let items = [];
if (payload.album_id || payload.album_name || payload.album_path) {{
  let album = null;
  if (payload.album_id) {{
    const matches = app.albums.whose({{ id: payload.album_id }})();
    album = matches.length > 0 ? matches[0] : null;
  }} else if (payload.album_path) {{
    for (const folderRow of folderRows) {{
      const match = folderRow.albums.find((row) => row.path === payload.album_path);
      if (match) {{
        album = match.album;
        break;
      }}
    }}
    if (!album) {{
      const topLevelAlbum = app.albums().find((candidate) => candidate.name() === payload.album_path && !candidate.parent());
      album = topLevelAlbum || null;
    }}
  }} else {{
    album = app.albums.byName(payload.album_name);
    if (!album.exists()) {{
      album = null;
    }}
  }}
  if (!album) {{
    return {{ ok: false, error: "album not found" }};
  }}
  scopeType = "album";
  scopeName = album.name();
  items = album.mediaItems();
}} else if (payload.folder_id || payload.folder_path) {{
  const targetFolder = folderRows.find((row) => row.id === payload.folder_id || row.path === payload.folder_path);
  if (!targetFolder) {{
    return {{ ok: false, error: "folder not found" }};
  }}
  scopeType = "folder";
  scopeName = targetFolder.path;
  const targetFolders = folderRows.filter((row) => row.path === targetFolder.path || row.path.startsWith(`${{targetFolder.path}}/`));
  const byId = new Map();
  for (const row of targetFolders) {{
    for (const albumRow of row.albums) {{
      for (const item of albumRow.album.mediaItems()) {{
        byId.set(item.id(), item);
      }}
    }}
  }}
  items = Array.from(byId.values());
}} else {{
  items = app.mediaItems();
}}
const results = [];
for (const item of items) {{
  const keywords = item.keywords() || [];
  const row = {{
    id: item.id(),
    title: valueOrNull(item.name()),
    filename: valueOrNull(item.filename()),
    description: valueOrNull(item.description()),
    date: iso(item.date()),
    favorite: !!item.favorite(),
    keywords: keywords.map((keyword) => keyword.toString()),
    width: valueOrNull(item.width()),
    height: valueOrNull(item.height()),
    size: valueOrNull(item.size()),
    location: valueOrNull(item.location()),
    scope_type: scopeType,
    scope_name: scopeName,
  }};
  if (start && row.date && new Date(row.date) < start) {{
    continue;
  }}
  if (end && row.date && new Date(row.date) > end) {{
    continue;
  }}
  if (payload.favorite !== null && payload.favorite !== undefined && row.favorite !== payload.favorite) {{
    continue;
  }}
  const haystack = lower(`${{row.title || ""}}\\n${{row.filename || ""}}\\n${{row.description || ""}}\\n${{row.keywords.join(" ")}}`);
  if (query && !haystack.includes(query)) {{
    continue;
  }}
  results.push(row);
  if (!payload.sort_by && results.length >= limit) {{
    break;
  }}
}}
if (payload.sort_by === "date_asc") {{
  results.sort((a, b) => compareIsoAsc(a.date, b.date));
}} else if (payload.sort_by === "filename_asc") {{
  results.sort((a, b) => compareTextAsc(a.filename, b.filename));
}} else if (payload.sort_by === "date_desc") {{
  results.sort((a, b) => compareIsoDesc(a.date, b.date));
}}
results.splice(limit);
return {{ ok: true, results, count: results.length }};
"""
        return self._run_jxa_json(
            script,
            payload={
                "album_id": album_id,
                "album_name": album_name,
                "album_path": album_path,
                "folder_id": folder_id,
                "folder_path": folder_path,
                "query": query,
                "start": start,
                "end": end,
                "favorite": favorite,
                "sort_by": sort_by,
                "limit": limit,
            },
        )

    def photos_create_album(
        self,
        *,
        album_name: str,
        parent_folder_id: str | None,
        parent_folder_path: str | None,
    ) -> dict[str, Any]:
        """Create a Photos album."""
        if parent_folder_path and not parent_folder_id:
            resolved = self._resolve_photo_folder(
                folder_id=None,
                folder_path=parent_folder_path,
            )
            if not resolved.get("ok"):
                return resolved
            parent_folder_id = resolved["folder"]["id"]
        env = {"PARENT_FOLDER_ID": parent_folder_id or ""}
        script = f"""
set albumName to {_applescript_utf8_file_read("ALBUM_NAME")}
set parentFolderId to system attribute "PARENT_FOLDER_ID"
tell application "Photos"
  if parentFolderId is "" then
    set targetAlbum to make new album named albumName
  else
    set targetFolder to first folder whose id is parentFolderId
    set targetAlbum to make new album named albumName at targetFolder
  end if
  return id of targetAlbum
end tell
"""
        album_id = self._run_applescript(
            script,
            env=env,
            utf8_files={"ALBUM_NAME": album_name},
        )
        return self._photos_get_album(album_id=album_id)

    def photos_add_to_album(
        self,
        *,
        album_id: str | None,
        album_name: str | None,
        album_path: str | None,
        media_ids: list[str],
    ) -> dict[str, Any]:
        """Add media items to an album."""
        target = self._resolve_photo_album(
            album_id=album_id,
            album_name=album_name,
            album_path=album_path,
        )
        if not target.get("ok"):
            return target
        env = {
            "ALBUM_ID": target["album"]["id"],
            "MEDIA_IDS": "\n".join(media_ids),
        }
        script = """
set albumId to system attribute "ALBUM_ID"
set mediaIdsText to system attribute "MEDIA_IDS"
tell application "Photos"
  set targetAlbum to first album whose id is albumId
  set targetItems to {}
  repeat with mediaId in paragraphs of mediaIdsText
    if mediaId is not "" then
      set end of targetItems to (first media item whose id is mediaId)
    end if
  end repeat
  add targetItems to targetAlbum
  return count of media items of targetAlbum
end tell
"""
        count = int(self._run_applescript(script, env=env))
        result = self._photos_get_album(album_id=target["album"]["id"])
        if result.get("ok"):
            result["album"]["count"] = count
        return result

    def photos_export(
        self,
        *,
        media_ids: list[str],
        destination_dir: str | None,
        use_originals: bool,
    ) -> dict[str, Any]:
        """Export Photos media to files."""
        export_dir = self._prepare_export_dir(destination_dir)
        before = {path.name for path in export_dir.iterdir()} if export_dir.exists() else set()
        export_dir.mkdir(parents=True, exist_ok=True)
        env = {
            "MEDIA_IDS": "\n".join(media_ids),
            "USE_ORIGINALS": "1" if use_originals else "0",
        }
        script = f"""
set mediaIdsText to system attribute "MEDIA_IDS"
set exportDirText to {_applescript_utf8_file_read("EXPORT_DIR")}
set exportDir to POSIX file exportDirText
set useOriginals to (system attribute "USE_ORIGINALS") is "1"
tell application "Photos"
  set targetItems to {{}}
  repeat with mediaId in paragraphs of mediaIdsText
    if mediaId is not "" then
      set end of targetItems to (first media item whose id is mediaId)
    end if
  end repeat
  if useOriginals then
    export targetItems to exportDir with using originals
  else
    export targetItems to exportDir
  end if
end tell
"""
        self._run_applescript(
            script,
            env=env,
            utf8_files={"EXPORT_DIR": str(export_dir)},
        )
        files = sorted(
            str(path)
            for path in export_dir.iterdir()
            if path.is_file() and path.name not in before
        )
        return {
            "ok": True,
            "destination_dir": str(export_dir),
            "files": files,
            "count": len(files),
        }

    def _resolve_list_spec(
        self,
        *,
        list_id: str | None,
        list_name: str | None,
        list_path: str | None,
    ) -> dict[str, Any]:
        """Resolve a reminders list."""
        result = self._run_jxa_json(
            """
const app = Application("Reminders");
const payload = readPayload();
let list = null;
if (payload.list_id) {
  const matches = app.lists.whose({ id: payload.list_id })();
  if (matches.length > 0) {
    list = matches[0];
  }
} else if (payload.list_name) {
  list = app.lists.byName(payload.list_name);
  if (!list.exists()) {
    list = null;
  }
} else if (payload.list_path) {
  for (const account of app.accounts()) {
    for (const candidate of account.lists()) {
      const path = `${account.name()}/${candidate.name()}`;
      if (path === payload.list_path) {
        list = candidate;
        break;
      }
    }
    if (list) {
      break;
    }
  }
} else {
  list = app.defaultList();
}
if (!list) {
  return { ok: false, error: "reminders list not found" };
}
return {
  ok: true,
  list_id: list.id(),
  list_name: list.name(),
  list_path: `${list.container().name()}/${list.name()}`,
};
""",
            payload={
                "list_id": list_id,
                "list_name": list_name,
                "list_path": list_path,
            },
        )
        return result

    def _resolve_note_folder(
        self,
        *,
        folder_id: str | None,
        folder_path: str | None,
    ) -> dict[str, Any]:
        """Resolve a Notes folder."""
        return self._run_jxa_json(
            """
const app = Application("Notes");
const payload = readPayload();
function flattenFolders(folder, accountName, parentPath) {
  const path = parentPath ? `${parentPath}/${folder.name()}` : `${accountName}/${folder.name()}`;
  let rows = [{ id: folder.id(), name: folder.name(), account: accountName, path }];
  for (const child of folder.folders()) {
    rows = rows.concat(flattenFolders(child, accountName, path));
  }
  return rows;
}
let folders = [];
for (const account of app.accounts()) {
  for (const folder of account.folders()) {
    folders = folders.concat(flattenFolders(folder, account.name(), ""));
  }
}
let target = null;
if (payload.folder_id) {
  target = folders.find((row) => row.id === payload.folder_id) || null;
} else if (payload.folder_path) {
  target = folders.find((row) => row.path === payload.folder_path) || null;
}
if (!target) {
  return { ok: false, error: "notes folder not found" };
}
return { ok: true, folder_id: target.id, folder_path: target.path, account: target.account, folder_name: target.name };
""",
            payload={"folder_id": folder_id, "folder_path": folder_path},
            operation="notes.resolve_folder",
            log_details={"folder_id": folder_id, "folder_path": folder_path},
        )

    def _resolve_photo_album(
        self,
        *,
        album_id: str | None,
        album_name: str | None,
        album_path: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a Photos album."""
        if album_id:
            return self._photos_get_album(album_id=album_id)
        if album_path:
            result = self._run_jxa_json(
                """
const app = Application("Photos");
const payload = readPayload();
function flattenFolder(folder, parentPath) {
  const path = parentPath ? `${parentPath}/${folder.name()}` : folder.name();
  let rows = folder.albums().map((album) => ({
    id: album.id(),
    name: album.name(),
    path: `${path}/${album.name()}`,
    count: album.mediaItems().length,
    parent_folder_id: folder.id(),
    parent_folder_name: folder.name(),
  }));
  for (const child of folder.folders()) {
    rows = rows.concat(flattenFolder(child, path));
  }
  return rows;
}
let albums = app.albums()
  .filter((album) => !album.parent())
  .map((album) => ({
    id: album.id(),
    name: album.name(),
    path: album.name(),
    count: album.mediaItems().length,
    parent_folder_id: null,
    parent_folder_name: null,
  }));
for (const folder of app.folders()) {
  try {
    if (!folder.parent()) {
      albums = albums.concat(flattenFolder(folder, ""));
    }
  } catch (error) {}
}
const target = albums.find((album) => album.path === payload.album_path);
if (!target) {
  return { ok: false, error: `album not found: ${payload.album_path}` };
}
return { ok: true, album: target };
""",
                payload={"album_path": album_path},
            )
            return result
        if album_name:
            result = self._run_jxa_json(
                """
const app = Application("Photos");
const payload = readPayload();
const album = app.albums.byName(payload.album_name);
if (!album.exists()) {
  return { ok: false, error: `album not found: ${payload.album_name}` };
}
return { ok: true, album: { id: album.id(), name: album.name(), count: album.mediaItems().length } };
""",
                payload={"album_name": album_name},
            )
            return result
        return {"ok": False, "error": "album_id or album_name is required"}

    def _resolve_photo_folder(
        self,
        *,
        folder_id: str | None,
        folder_path: str | None,
    ) -> dict[str, Any]:
        """Resolve a Photos folder."""
        return self._run_jxa_json(
            """
const app = Application("Photos");
const payload = readPayload();
function flattenFolder(folder, parentPath) {
  const path = parentPath ? `${parentPath}/${folder.name()}` : folder.name();
  let rows = [{ id: folder.id(), name: folder.name(), path }];
  for (const child of folder.folders()) {
    rows = rows.concat(flattenFolder(child, path));
  }
  return rows;
}
let folders = [];
for (const folder of app.folders()) {
  try {
    if (!folder.parent()) {
      folders = folders.concat(flattenFolder(folder, ""));
    }
  } catch (error) {}
}
let target = null;
if (payload.folder_id) {
  target = folders.find((row) => row.id === payload.folder_id) || null;
} else if (payload.folder_path) {
  target = folders.find((row) => row.path === payload.folder_path) || null;
}
if (!target) {
  return { ok: false, error: "folder not found" };
}
return { ok: true, folder: target };
""",
            payload={"folder_id": folder_id, "folder_path": folder_path},
        )

    def photos_get_album(
        self,
        *,
        album_id: str | None,
        album_name: str | None,
        album_path: str | None = None,
    ) -> dict[str, Any]:
        """Fetch one album by id or exact name."""
        return self._resolve_photo_album(
            album_id=album_id,
            album_name=album_name,
            album_path=album_path,
        )

    def photos_get_media(self, *, media_ids: list[str]) -> dict[str, Any]:
        """Fetch media metadata by ids."""
        return self._run_jxa_json(
            """
const app = Application("Photos");
const payload = readPayload();
const results = [];
for (const mediaId of payload.media_ids || []) {
  const matches = app.mediaItems.whose({ id: mediaId })();
  if (matches.length === 0) {
    continue;
  }
  const item = matches[0];
  const keywords = item.keywords() || [];
  results.push({
    id: item.id(),
    title: valueOrNull(item.name()),
    filename: valueOrNull(item.filename()),
    description: valueOrNull(item.description()),
    date: iso(item.date()),
    favorite: !!item.favorite(),
    keywords: keywords.map((keyword) => keyword.toString()),
    width: valueOrNull(item.width()),
    height: valueOrNull(item.height()),
    size: valueOrNull(item.size()),
    location: valueOrNull(item.location()),
  });
}
return { ok: true, results, count: results.length };
""",
            payload={"media_ids": media_ids},
        )

    def _photos_get_album(self, *, album_id: str) -> dict[str, Any]:
        """Fetch one album by id."""
        return self._run_jxa_json(
            """
const app = Application("Photos");
const payload = readPayload();
const matches = app.albums.whose({ id: payload.album_id })();
if (matches.length === 0) {
  return { ok: false, error: `album not found: ${payload.album_id}` };
}
const album = matches[0];
const parent = album.parent();
return {
  ok: true,
  album: {
    id: album.id(),
    name: album.name(),
    count: album.mediaItems().length,
    parent_folder_id: parent ? parent.id() : null,
    parent_folder_name: parent ? parent.name() : null,
  },
};
""",
            payload={"album_id": album_id},
        )

    def _prepare_export_dir(self, destination_dir: str | None) -> Path:
        """Resolve and validate the Photos export directory."""
        if destination_dir:
            candidate = Path(destination_dir)
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            candidate = self._base_dir / self._photos_export_dir / stamp
        if not is_path_allowed(str(candidate), self._allowed_paths, self._base_dir):
            raise ValueError(
                f"destination_dir is outside allowed paths: {candidate}"
            )
        return candidate.resolve()

    def _run_jxa_json(
        self,
        body: str,
        *,
        payload: dict[str, Any] | None = None,
        operation: str | None = None,
        log_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a JXA script and parse JSON output."""
        script = f"""
ObjC.import("stdlib");
function readPayload() {{
  const raw = $.getenv("CHAT_AGENT_APP_TOOL_PAYLOAD");
  return raw ? JSON.parse(ObjC.unwrap(raw)) : {{}};
}}
function iso(value) {{
  if (!value) {{
    return null;
  }}
  try {{
    return value.toISOString();
  }} catch (error) {{
    return null;
  }}
}}
function lower(value) {{
  return (value || "").toString().toLowerCase();
}}
function valueOrNull(value) {{
  return value === undefined ? null : value;
}}
function clampLimit(value, maxLimit) {{
  const raw = value || maxLimit;
  return Math.max(1, Math.min(raw, maxLimit));
}}
function compareIsoAsc(a, b) {{
  if (!a && !b) return 0;
  if (!a) return 1;
  if (!b) return -1;
  return new Date(a) - new Date(b);
}}
function compareIsoDesc(a, b) {{
  return compareIsoAsc(b, a);
}}
function compareTextAsc(a, b) {{
  return (a || "").localeCompare(b || "");
}}
function main() {{
{body}
}}
JSON.stringify(main());
"""
        env = os.environ.copy()
        env["CHAT_AGENT_APP_TOOL_PAYLOAD"] = json.dumps(payload or {})
        started = time.monotonic()
        try:
            completed = subprocess.run(
                ["osascript", "-l", "JavaScript"],
                input=script,
                text=True,
                capture_output=True,
                env=env,
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - started
            logger.warning(
                "macOS app tool timeout engine=jxa operation=%s elapsed=%.2fs details=%s",
                operation or "unknown",
                elapsed,
                _format_app_tool_log_details(log_details or payload),
            )
            raise RuntimeError(
                f"{operation or 'macOS app tool'} timed out after {self._timeout_seconds:.1f} seconds"
            ) from exc
        elapsed = time.monotonic() - started
        if elapsed >= _SLOW_APP_TOOL_SECONDS:
            logger.warning(
                "macOS app tool slow engine=jxa operation=%s elapsed=%.2fs details=%s",
                operation or "unknown",
                elapsed,
                _format_app_tool_log_details(log_details or payload),
            )
        if completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout).strip()
            logger.warning(
                "macOS app tool failure engine=jxa operation=%s elapsed=%.2fs details=%s error=%s",
                operation or "unknown",
                elapsed,
                _format_app_tool_log_details(log_details or payload),
                stderr or "JXA command failed",
            )
            raise RuntimeError(stderr or "JXA command failed")
        output = completed.stdout.strip()
        if not output:
            logger.warning(
                "macOS app tool empty-output engine=jxa operation=%s elapsed=%.2fs details=%s",
                operation or "unknown",
                elapsed,
                _format_app_tool_log_details(log_details or payload),
            )
            raise RuntimeError("JXA command returned no output")
        return json.loads(output)

    def _run_applescript(
        self,
        script: str,
        *,
        env: dict[str, str] | None = None,
        utf8_files: dict[str, str] | None = None,
        operation: str | None = None,
        log_details: dict[str, Any] | None = None,
    ) -> str:
        """Run an AppleScript snippet and return stdout."""
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="chat-agent-osascript-") as temp_dir:
                if utf8_files:
                    temp_root = Path(temp_dir)
                    for key, value in utf8_files.items():
                        path = temp_root / f"{key}.txt"
                        path.write_text(value, encoding="utf-8")
                        merged_env[f"{key}_FILE"] = str(path)
                completed = subprocess.run(
                    ["osascript"],
                    input=script,
                    text=True,
                    capture_output=True,
                    env=merged_env,
                    timeout=self._timeout_seconds,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - started
            logger.warning(
                "macOS app tool timeout engine=applescript operation=%s elapsed=%.2fs details=%s",
                operation or "unknown",
                elapsed,
                _format_app_tool_log_details(log_details),
            )
            raise RuntimeError(
                f"{operation or 'macOS app tool'} timed out after {self._timeout_seconds:.1f} seconds"
            ) from exc
        elapsed = time.monotonic() - started
        if elapsed >= _SLOW_APP_TOOL_SECONDS:
            logger.warning(
                "macOS app tool slow engine=applescript operation=%s elapsed=%.2fs details=%s",
                operation or "unknown",
                elapsed,
                _format_app_tool_log_details(log_details),
            )
        if completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout).strip()
            logger.warning(
                "macOS app tool failure engine=applescript operation=%s elapsed=%.2fs details=%s error=%s",
                operation or "unknown",
                elapsed,
                _format_app_tool_log_details(log_details),
                stderr or "AppleScript command failed",
            )
            raise RuntimeError(stderr or "AppleScript command failed")
        return completed.stdout.strip()


def create_calendar_tool(bridge: MacOSAppBridge) -> Callable[..., str]:
    """Create calendar_tool bound to the bridge."""

    def calendar_tool(
        action: str,
        calendar: str | None = None,
        calendars: list[str] | None = None,
        event_uid: str | None = None,
        exclude_event_uid: str | None = None,
        query: str | None = None,
        title: str | None = None,
        notes: str | None = None,
        location: str | None = None,
        url: str | None = None,
        start: str | None = None,
        end: str | None = None,
        all_day: bool | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
    ) -> str:
        try:
            if action == "catalog":
                return _json_output(bridge.calendar_catalog())
            if action == "search":
                return _json_output(
                    bridge.calendar_search(
                        calendar=calendar,
                        calendars=calendars,
                        query=query,
                        start=start,
                        end=end,
                        all_day=all_day,
                        sort_by=sort_by,
                        limit=limit,
                    )
                )
            if action == "conflicts":
                if not start or not end:
                    return _error("'start' and 'end' are required for conflicts")
                start_dt = _parse_local_datetime(start, field_name="start")
                end_dt = _parse_local_datetime(end, field_name="end")
                if end_dt < start_dt:
                    return _error("'end' must be after or equal to 'start'")
                return _json_output(
                    bridge.calendar_conflicts(
                        calendar=calendar,
                        calendars=calendars,
                        start=start,
                        end=end,
                        exclude_event_uid=exclude_event_uid,
                        all_day=all_day,
                        limit=limit,
                    )
                )
            if action == "get":
                if not event_uid:
                    return _error("'event_uid' is required for get")
                return _json_output(
                    bridge.calendar_get(event_uid=event_uid, calendar=calendar)
                )
            if action == "create":
                if not calendar:
                    return _error("'calendar' is required for create")
                if not title:
                    return _error("'title' is required for create")
                if not start or not end:
                    return _error("'start' and 'end' are required for create")
                start_dt = _parse_local_datetime(start, field_name="start")
                end_dt = _parse_local_datetime(end, field_name="end")
                if end_dt < start_dt:
                    return _error("'end' must be after or equal to 'start'")
                return _json_output(
                    bridge.calendar_create(
                        calendar=calendar,
                        title=title,
                        start=start_dt,
                        end=end_dt,
                        notes=notes,
                        location=location,
                        url=url,
                        all_day=all_day,
                    )
                )
            if action == "update":
                if not event_uid:
                    return _error("'event_uid' is required for update")
                start_dt = _parse_local_datetime(start, field_name="start") if start else None
                end_dt = _parse_local_datetime(end, field_name="end") if end else None
                if start_dt is not None and end_dt is not None and end_dt < start_dt:
                    return _error("'end' must be after or equal to 'start'")
                if (
                    title is None
                    and notes is None
                    and location is None
                    and url is None
                    and start_dt is None
                    and end_dt is None
                    and all_day is None
                ):
                    return _error("update requires at least one field to change")
                return _json_output(
                    bridge.calendar_update(
                        event_uid=event_uid,
                        calendar=calendar,
                        title=title,
                        start=start_dt,
                        end=end_dt,
                        notes=notes,
                        location=location,
                        url=url,
                        all_day=all_day,
                    )
                )
            return _error(f"unknown action '{action}'")
        except Exception as exc:  # pragma: no cover - surfaced to user
            return _error(str(exc))

    return calendar_tool


def create_reminders_tool(bridge: MacOSAppBridge) -> Callable[..., str]:
    """Create reminders_tool bound to the bridge."""

    def reminders_tool(
        action: str,
        list_id: str | None = None,
        list_name: str | None = None,
        list_path: str | None = None,
        reminder_id: str | None = None,
        query: str | None = None,
        title: str | None = None,
        notes: str | None = None,
        due: str | None = None,
        due_start: str | None = None,
        due_end: str | None = None,
        priority: int | None = None,
        priority_min: int | None = None,
        priority_max: int | None = None,
        flagged: bool | None = None,
        completed: bool | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
    ) -> str:
        try:
            if action == "catalog":
                return _json_output(bridge.reminders_catalog())
            if action == "search":
                return _json_output(
                    bridge.reminders_search(
                        list_id=list_id,
                        list_name=list_name,
                        list_path=list_path,
                        query=query,
                        due_start=due_start,
                        due_end=due_end,
                        completed=completed,
                        flagged=flagged,
                        priority_min=priority_min,
                        priority_max=priority_max,
                        sort_by=sort_by,
                        limit=limit,
                    )
                )
            if action == "get":
                if not reminder_id:
                    return _error("'reminder_id' is required for get")
                return _json_output(bridge.reminders_get(reminder_id=reminder_id))
            if action == "create":
                if not title:
                    return _error("'title' is required for create")
                due_dt = _parse_local_datetime(due, field_name="due") if due else None
                return _json_output(
                    bridge.reminders_create(
                        list_id=list_id,
                        list_name=list_name,
                        list_path=list_path,
                        title=title,
                        notes=notes,
                        due=due_dt,
                        priority=priority,
                        flagged=flagged,
                    )
                )
            if action in {"update", "complete"}:
                if not reminder_id:
                    return _error("'reminder_id' is required for update/complete")
                due_dt = _parse_local_datetime(due, field_name="due") if due else None
                if action == "complete" and completed is None:
                    completed = True
                if (
                    title is None
                    and notes is None
                    and due_dt is None
                    and priority is None
                    and flagged is None
                    and completed is None
                ):
                    return _error("update requires at least one field to change")
                return _json_output(
                    bridge.reminders_update(
                        reminder_id=reminder_id,
                        title=title,
                        notes=notes,
                        due=due_dt,
                        priority=priority,
                        flagged=flagged,
                        completed=completed,
                    )
                )
            return _error(f"unknown action '{action}'")
        except Exception as exc:  # pragma: no cover - surfaced to user
            return _error(str(exc))

    return reminders_tool


def create_notes_tool(bridge: MacOSAppBridge) -> Callable[..., str]:
    """Create notes_tool bound to the bridge."""

    def notes_tool(
        action: str,
        account: str | None = None,
        folder_id: str | None = None,
        folder_path: str | None = None,
        target_folder_id: str | None = None,
        target_folder_path: str | None = None,
        note_id: str | None = None,
        query: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        modified_after: str | None = None,
        modified_before: str | None = None,
        title: str | None = None,
        body: str | None = None,
        template_markdown: str | None = None,
        variables: dict[str, Any] | None = None,
        images: dict[str, Any] | None = None,
        append: bool = False,
        sort_by: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> str:
        try:
            text_variables = _coerce_template_mapping(variables, field_name="variables")
            image_variables = _coerce_template_mapping(images, field_name="images")
            if action == "catalog":
                return _json_output(bridge.notes_catalog())
            if action == "search":
                return _json_output(
                    bridge.notes_search(
                        account=account,
                        folder_id=folder_id,
                        folder_path=folder_path,
                        query=query,
                        created_after=created_after,
                        created_before=created_before,
                        modified_after=modified_after,
                        modified_before=modified_before,
                        sort_by=sort_by,
                        limit=limit,
                        offset=offset,
                    )
                )
            if action == "get":
                if not note_id:
                    return _error("'note_id' is required for get")
                return _json_output(bridge.notes_get(note_id=note_id))
            if action == "create":
                if body is None and template_markdown is None:
                    return _error("'body' or 'template_markdown' is required for create")
                if not folder_id and not folder_path:
                    return _error("'folder_id' or 'folder_path' is required for create")
                return _json_output(
                    bridge.notes_create(
                        folder_id=folder_id,
                        folder_path=folder_path,
                        title=title,
                        body=body,
                        template_markdown=template_markdown,
                        variables=text_variables,
                        images=image_variables,
                    )
                )
            if action == "update":
                if not note_id:
                    return _error("'note_id' is required for update")
                if body is None and template_markdown is None:
                    return _error("'body' or 'template_markdown' is required for update")
                return _json_output(
                    bridge.notes_update(
                        note_id=note_id,
                        title=title,
                        body=body,
                        template_markdown=template_markdown,
                        variables=text_variables,
                        images=image_variables,
                        append=append,
                    )
                )
            if action == "move":
                if not note_id:
                    return _error("'note_id' is required for move")
                if not target_folder_id and not target_folder_path:
                    return _error("'target_folder_id' or 'target_folder_path' is required for move")
                return _json_output(
                    bridge.notes_move(
                        note_id=note_id,
                        target_folder_id=target_folder_id,
                        target_folder_path=target_folder_path,
                    )
                )
            return _error(f"unknown action '{action}'")
        except Exception as exc:  # pragma: no cover - surfaced to user
            return _error(str(exc))

    return notes_tool


def create_photos_tool(bridge: MacOSAppBridge) -> Callable[..., str]:
    """Create photos_tool bound to the bridge."""

    def photos_tool(
        action: str,
        album_id: str | None = None,
        album_name: str | None = None,
        album_path: str | None = None,
        folder_id: str | None = None,
        folder_path: str | None = None,
        parent_folder_id: str | None = None,
        parent_folder_path: str | None = None,
        query: str | None = None,
        start: str | None = None,
        end: str | None = None,
        favorite: bool | None = None,
        sort_by: str | None = None,
        media_ids: list[str] | None = None,
        destination_dir: str | None = None,
        use_originals: bool = True,
        limit: int | None = None,
    ) -> str:
        try:
            if action == "catalog":
                return _json_output(bridge.photos_catalog())
            if action == "search":
                return _json_output(
                    bridge.photos_search(
                        album_id=album_id,
                        album_name=album_name,
                        album_path=album_path,
                        folder_id=folder_id,
                        folder_path=folder_path,
                        query=query,
                        start=start,
                        end=end,
                        favorite=favorite,
                        sort_by=sort_by,
                        limit=limit,
                    )
                )
            if action == "get_media":
                if not media_ids:
                    return _error("'media_ids' is required for get_media")
                return _json_output(bridge.photos_get_media(media_ids=media_ids))
            if action == "get_album":
                if not album_id and not album_name and not album_path:
                    return _error("'album_id', 'album_name', or 'album_path' is required for get_album")
                return _json_output(
                    bridge.photos_get_album(
                        album_id=album_id,
                        album_name=album_name,
                        album_path=album_path,
                    )
                )
            if action == "create_album":
                if not album_name:
                    return _error("'album_name' is required for create_album")
                return _json_output(
                    bridge.photos_create_album(
                        album_name=album_name,
                        parent_folder_id=parent_folder_id,
                        parent_folder_path=parent_folder_path,
                    )
                )
            if action == "add_to_album":
                if not media_ids:
                    return _error("'media_ids' is required for add_to_album")
                if not album_id and not album_name and not album_path:
                    return _error("'album_id', 'album_name', or 'album_path' is required for add_to_album")
                return _json_output(
                    bridge.photos_add_to_album(
                        album_id=album_id,
                        album_name=album_name,
                        album_path=album_path,
                        media_ids=media_ids,
                    )
                )
            if action == "export":
                if not media_ids:
                    return _error("'media_ids' is required for export")
                return _json_output(
                    bridge.photos_export(
                        media_ids=media_ids,
                        destination_dir=destination_dir,
                        use_originals=use_originals,
                    )
                )
            return _error(f"unknown action '{action}'")
        except Exception as exc:  # pragma: no cover - surfaced to user
            return _error(str(exc))

    return photos_tool
