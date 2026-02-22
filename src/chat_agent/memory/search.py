"""Memory search tool backed by a sub-LLM that reads memory indexes."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..llm.base import LLMClient
from ..llm.schema import Message, ToolDefinition, ToolParameter
from ..llm.json_extract import extract_json_object

logger = logging.getLogger(__name__)

_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "relevance": {"type": "string"},
                },
                "required": ["path", "relevance"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}

_DEFAULT_PARSE_RETRY_PROMPT = (
    "你上次的輸出格式無效。\n"
    "僅回傳一個 JSON 物件，包含 \"results\" 陣列，"
    "每個元素為 {\"path\": \"...\", \"relevance\": \"...\"} 物件。\n"
    "不要輸出工具呼叫、markdown 區塊、推理過程或說明文字。"
)

_STAGE1_USER_PROMPT_TEMPLATE = (
    "STAGE: index_candidate_selection\n"
    "任務：根據查詢與記憶索引，選出可能相關的內容檔案。\n\n"
    "查詢：{query}\n\n"
    "{context}"
)

_STAGE2_USER_PROMPT_TEMPLATE = (
    "STAGE: content_refinement\n"
    "任務：根據候選檔案的完整內容，篩選出真正相關的結果。\n"
    "只能回傳以下候選清單中的路徑。\n\n"
    "查詢：{query}\n\n"
    "{context}"
)


class MemorySearchResult(BaseModel):
    """A single search result from memory."""

    path: str
    relevance: str


MEMORY_SEARCH_DEFINITION = ToolDefinition(
    name="memory_search",
    description=(
        "Search memory for content relevant to a topic or question. "
        "Returns matching snippets from memory files with surrounding context. "
        "Usually sufficient without follow-up read_file. "
        "Call this when you need to recall past information, knowledge, "
        "experiences, or facts about people."
    ),
    parameters={
        "query": ToolParameter(
            type="string",
            description=(
                "What you are looking for in memory. Use 3-5 specific keywords. "
                "Avoid common terms that appear everywhere. "
                "Examples: 'APCS teaching schedule', "
                "'medication side effects', 'cooking skills'."
            ),
        ),
    },
    required=["query"],
)


class MemorySearchAgent:
    """Sub-agent that searches memory indexes and returns relevant paths."""

    def __init__(
        self,
        client: LLMClient,
        system_prompt: str,
        memory_dir: Path,
        parse_retries: int = 1,
        parse_retry_prompt: str | None = None,
        context_bytes_limit: int | None = None,
        max_results: int | None = None,
    ):
        self.client = client
        self.system_prompt = system_prompt
        self.memory_dir = memory_dir
        self.parse_retries = parse_retries
        self.parse_retry_prompt = parse_retry_prompt or _DEFAULT_PARSE_RETRY_PROMPT
        self.context_bytes_limit = context_bytes_limit
        self.max_results = max_results
        self.last_raw_response: str | None = None

    def search(
        self,
        query: str,
        *,
        propagate_errors: bool = False,
    ) -> list[MemorySearchResult]:
        """Search memory for files relevant to query.

        Returns list of MemorySearchResult, empty on failure.
        """
        context = self._build_memory_context()
        stage1_messages = [
            Message(role="system", content=self.system_prompt),
            Message(
                role="user",
                content=_STAGE1_USER_PROMPT_TEMPLATE.format(
                    query=query,
                    context=context,
                ),
            ),
        ]
        stage1_results = self._run_search(
            stage1_messages, propagate_errors=propagate_errors,
        )
        if stage1_results is None:
            return []

        stage1_paths = self._filter_existing_content_paths(stage1_results)

        # Merge keyword-scanned candidates that Stage 1 missed
        keyword_hits = self._keyword_candidates(query)
        stage1_set = {item.path for item in stage1_paths}
        for hit in keyword_hits:
            if hit.path not in stage1_set:
                stage1_paths.append(hit)
                stage1_set.add(hit.path)

        stage1_fallback = self._apply_max_results(stage1_paths)
        if not stage1_paths:
            return []

        stage2_context = self._build_candidate_content_context(stage1_paths)
        stage2_messages = [
            Message(role="system", content=self.system_prompt),
            Message(
                role="user",
                content=_STAGE2_USER_PROMPT_TEMPLATE.format(
                    query=query,
                    context=stage2_context,
                ),
            ),
        ]
        stage2_results = self._run_search(
            stage2_messages, propagate_errors=propagate_errors,
        )
        if stage2_results is None:
            return stage1_fallback

        stage2_paths = self._filter_existing_content_paths(
            stage2_results,
            allowed_paths={item.path for item in stage1_paths},
        )
        return self._apply_max_results(stage2_paths)

    def _run_search(
        self,
        base_messages: list[Message],
        *,
        propagate_errors: bool = False,
    ) -> list[MemorySearchResult] | None:
        """Run search LLM with parse retries; return None when unresolved."""
        review_messages = list(base_messages)
        try:
            for attempt in range(self.parse_retries + 1):
                raw = self.client.chat(review_messages, response_schema=_SEARCH_SCHEMA)
                self.last_raw_response = raw
                is_final = attempt >= self.parse_retries
                results = self._parse_response(raw, final_attempt=is_final)
                if results is not None:
                    return results
                if attempt < self.parse_retries:
                    review_messages = [
                        *base_messages,
                        Message(role="user", content=self.parse_retry_prompt),
                    ]
            return None
        except Exception as e:
            if propagate_errors:
                raise
            logger.warning("Memory search failed: %s", e)
            self.last_raw_response = None
            return None

    def _build_memory_context(self) -> str:
        """Read all index.md files and list directory contents."""
        if not self.memory_dir.exists():
            return "(memory directory does not exist)"

        sections: list[str] = []
        total_size = 0

        for index_file in sorted(self.memory_dir.rglob("index.md")):
            rel_path = index_file.relative_to(self.memory_dir.parent)
            try:
                content = index_file.read_text(encoding="utf-8")
            except Exception:
                continue

            # List sibling files in same directory
            parent = index_file.parent
            siblings = sorted(
                f.name
                for f in parent.iterdir()
                if f.is_file() and f.name != "index.md"
            )
            file_list = ", ".join(siblings) if siblings else "(empty)"

            section = f"### {rel_path}\n{content.strip()}\n\nFiles: {file_list}"
            section_size = len(section.encode("utf-8"))

            if self.context_bytes_limit is not None:
                if section_size > self.context_bytes_limit:
                    continue
                if total_size + section_size > self.context_bytes_limit:
                    continue
            sections.append(section)
            total_size += section_size

        # Also note top-level files outside subdirectories
        top_files = sorted(
            f.name
            for f in self.memory_dir.iterdir()
            if f.is_file()
        )
        if top_files:
            sections.insert(0, f"### memory/ (top-level files)\n{', '.join(top_files)}")

        return "## Memory Index\n\n" + "\n\n".join(sections) if sections else "(no index files found)"

    def _build_candidate_content_context(self, candidates: list[MemorySearchResult]) -> str:
        """Build stage-2 context by embedding full candidate file contents."""
        sections: list[str] = ["## Candidate Files"]
        for item in candidates:
            abs_path = self.memory_dir.parent / item.path
            try:
                content = abs_path.read_text(encoding="utf-8")
            except Exception:
                continue
            sections.append(
                f"### {item.path}\n{content}"
            )
        if len(sections) == 1:
            return "(no readable candidate files)"
        return "\n\n".join(sections)

    def _keyword_candidates(
        self,
        query: str,
        min_matches: int = 2,
    ) -> list[MemorySearchResult]:
        """Scan content files for query tokens, return files with enough hits.

        Catches cases where Stage 1 LLM misses files due to name mismatch
        (e.g. Chinese query term vs English filename).
        """
        tokens = [t for t in query.split() if len(t) >= 2]
        if not tokens:
            return []
        # Single-token query: require 1 match
        threshold = 1 if len(tokens) <= 2 else min_matches

        results: list[MemorySearchResult] = []
        for md_file in sorted(self.memory_dir.rglob("*.md")):
            if md_file.name == "index.md":
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue
            content_lower = content.lower()
            matches = sum(1 for t in tokens if t.lower() in content_lower)
            if matches >= threshold:
                rel_path = str(md_file.relative_to(self.memory_dir.parent))
                results.append(MemorySearchResult(
                    path=rel_path,
                    relevance=f"keyword match ({matches}/{len(tokens)} terms)",
                ))
        return results

    def _filter_existing_content_paths(
        self,
        results: list[MemorySearchResult],
        *,
        allowed_paths: set[str] | None = None,
    ) -> list[MemorySearchResult]:
        """Filter to existing, non-index content files and keep order."""
        filtered: list[MemorySearchResult] = []
        seen_paths: set[str] = set()

        memory_root = self.memory_dir.resolve()
        for item in results:
            normalized_path = item.path.strip().replace("\\", "/")
            if normalized_path in seen_paths:
                continue
            if allowed_paths is not None and normalized_path not in allowed_paths:
                continue
            abs_path = (self.memory_dir.parent / normalized_path).resolve(strict=False)
            try:
                abs_path.relative_to(memory_root)
            except ValueError:
                continue
            if not abs_path.exists() or not abs_path.is_file():
                continue
            seen_paths.add(normalized_path)
            filtered.append(MemorySearchResult(path=normalized_path, relevance=item.relevance))
        return filtered

    def _apply_max_results(self, results: list[MemorySearchResult]) -> list[MemorySearchResult]:
        """Apply configurable max-results cap."""
        if self.max_results is None:
            return results
        return results[: self.max_results]

    def _parse_response(
        self,
        raw: str,
        *,
        final_attempt: bool,
    ) -> list[MemorySearchResult] | None:
        """Parse JSON from LLM response."""
        data = extract_json_object(raw)
        if data is None:
            log = logger.warning if final_attempt else logger.debug
            log("Failed to parse memory search response: %s", raw.strip()[:200])
            return None

        results_data = data.get("results")
        if not isinstance(results_data, list):
            log = logger.warning if final_attempt else logger.debug
            log("Invalid memory search schema: %s", str(data)[:200])
            return None

        results: list[MemorySearchResult] = []
        for item in results_data:
            if not isinstance(item, dict):
                continue
            path = item.get("path", "")
            relevance = item.get("relevance", "")
            if not isinstance(path, str):
                continue
            normalized_path = path.strip().replace("\\", "/")
            if not normalized_path.startswith("memory/"):
                continue
            if normalized_path == "memory/index.md" or normalized_path.endswith("/index.md"):
                continue
            results.append(
                MemorySearchResult(path=normalized_path, relevance=str(relevance))
            )

        return results


def create_memory_search(
    agent: MemorySearchAgent,
    allow_failure: bool = True,
) -> Callable[..., str]:
    """Create memory_search tool function bound to a MemorySearchAgent."""

    def memory_search(query: str = "", **kwargs: Any) -> str:
        q = query or kwargs.get("q", "") or kwargs.get("search", "")
        if not isinstance(q, str) or not q.strip():
            return "Error: query is required."
        results = agent.search(q.strip(), propagate_errors=not allow_failure)
        if not results:
            return "No relevant memory files found for this query."
        lines = [f"- `{r.path}`: {r.relevance}" for r in results]
        return "\n".join(lines)

    return memory_search
