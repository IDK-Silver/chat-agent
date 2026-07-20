"""Shared PDF text extraction utility."""

from __future__ import annotations


def extract_pdf_text(
    source: str | bytes,
    *,
    pages: list[int] | None = None,
) -> str:
    """Extract text from a PDF file or raw bytes, returning markdown-formatted text.

    Args:
        source: File path (str) or raw PDF bytes.
        pages: Optional 0-indexed page numbers to extract. ``None`` extracts all.

    Returns:
        Markdown text with ``## Page N`` headings per page.

    Raises:
        ValueError: On encrypted/corrupt PDF or missing dependency.
    """
    try:
        import pymupdf
    except ImportError:
        raise ValueError("PDF support requires pymupdf. Install with: uv add pymupdf")

    try:
        if isinstance(source, bytes):
            doc = pymupdf.open(stream=source, filetype="pdf")
        else:
            doc = pymupdf.open(source)
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    try:
        if doc.is_encrypted and not doc.authenticate(""):
            raise ValueError("PDF is encrypted and cannot be read")

        parts: list[str] = []
        for i, page in enumerate(doc):
            if pages is not None and i not in pages:
                continue
            text = page.get_text("text").strip()
            if text:
                parts.append(f"## Page {i + 1}\n\n{text}")

        if not parts:
            return "(No text content found in PDF)"
        return "\n\n".join(parts)
    finally:
        doc.close()
