"""Text formatting utilities for channel adapters."""

import re

# Code fences: ```lang\n...\n``` (entire block including content)
_CODE_FENCE_BLOCK_RE = re.compile(
    r"^```\w*\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL,
)

# Images before links: ![alt](url)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")

# Links: [text](url) -> text (url)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# Bold: **text** and __text__
_BOLD_ASTERISK_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_BOLD_UNDERSCORE_RE = re.compile(r"__(.+?)__", re.DOTALL)

# Italic: *text* and _text_ (underscore guarded against snake_case)
_ITALIC_ASTERISK_RE = re.compile(r"\*(.+?)\*", re.DOTALL)
_ITALIC_UNDERSCORE_RE = re.compile(r"(?<![a-zA-Z0-9])_(.+?)_(?![a-zA-Z0-9])")

# Inline code: `code`
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")

# Headers: # ... ###### at line start
_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)

# Blockquotes: > at line start
_BLOCKQUOTE_RE = re.compile(r"^>\s?", re.MULTILINE)

# Horizontal rules: --- / *** / ___
_HR_RE = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)

# Excessive blank lines
_BLANK_LINES_RE = re.compile(r"\n{3,}")

_PLACEHOLDER = "\x00CODEBLOCK{}\x00"
_PLACEHOLDER_RE = re.compile(r"\x00CODEBLOCK(\d+)\x00")


def markdown_to_plaintext(text: str) -> str:
    """Convert markdown-formatted text to clean plain text.

    Strips common markdown syntax while preserving readability.
    Designed for channels that cannot render markdown (email, SMS, etc.).
    """
    if not text:
        return text

    # 1. Extract code fence blocks into placeholders to protect content.
    blocks: list[str] = []

    def _stash(m: re.Match) -> str:
        idx = len(blocks)
        blocks.append(m.group(1).rstrip("\n"))
        return _PLACEHOLDER.format(idx)

    text = _CODE_FENCE_BLOCK_RE.sub(_stash, text)

    # 2. Process remaining markdown.
    # HR before bold/italic to prevent *** from being consumed as bold.
    text = _HR_RE.sub("", text)
    text = _IMAGE_RE.sub(r"\1", text)
    text = _LINK_RE.sub(r"\1 (\2)", text)
    text = _BOLD_ASTERISK_RE.sub(r"\1", text)
    text = _BOLD_UNDERSCORE_RE.sub(r"\1", text)
    text = _ITALIC_ASTERISK_RE.sub(r"\1", text)
    text = _ITALIC_UNDERSCORE_RE.sub(r"\1", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _HEADER_RE.sub("", text)
    text = _BLOCKQUOTE_RE.sub("", text)

    # 3. Restore code blocks.
    text = _PLACEHOLDER_RE.sub(lambda m: blocks[int(m.group(1))], text)

    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()
