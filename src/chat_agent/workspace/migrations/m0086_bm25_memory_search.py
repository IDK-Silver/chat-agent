"""Replace LLM memory search with BM25 + jieba deterministic search."""

import shutil
from pathlib import Path

from .base import Migration


class M0086Bm25MemorySearch(Migration):
    """Deploy BM25 memory search and update brain prompt."""

    version = "0.52.0"
    summary = "memory search 改用 BM25 確定性搜尋，直接回傳內容片段而非路徑"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents/brain/prompts/system.md"
        dst = kernel_dir / "agents/brain/prompts/system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
