"""Tests for MemorySearchAgent and create_memory_search."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from chat_agent.memory.search import MemorySearchAgent, create_memory_search


def _make_memory(tmp_path: Path) -> Path:
    """Create a minimal memory directory with indexes and content files."""
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "short-term.md").write_text("recent events", encoding="utf-8")

    agent_dir = mem / "agent"
    agent_dir.mkdir()
    (agent_dir / "index.md").write_text("# Agent Index\n- persona.md\n- inner-state.md", encoding="utf-8")
    (agent_dir / "persona.md").write_text("persona data", encoding="utf-8")
    (agent_dir / "inner-state.md").write_text("mood data", encoding="utf-8")

    people_dir = mem / "people"
    people_dir.mkdir()
    (people_dir / "index.md").write_text("# People Index\n- user-yufeng.md", encoding="utf-8")
    (people_dir / "user-yufeng.md").write_text("user data", encoding="utf-8")

    return mem


def _payload(paths: list[str]) -> str:
    return json.dumps({
        "results": [{"path": path, "relevance": f"match:{path}"} for path in paths]
    })


class TestMemorySearchAgent:
    def test_search_two_stage_returns_stage2_results(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            _payload(["memory/agent/persona.md", "memory/people/user-yufeng.md"]),
            _payload(["memory/people/user-yufeng.md"]),
        ]

        agent = MemorySearchAgent(mock_client, "system prompt", mem)
        results = agent.search("who is yufeng")

        assert [r.path for r in results] == ["memory/people/user-yufeng.md"]
        assert mock_client.chat.call_count == 2

    def test_search_stage2_parse_failure_falls_back_to_stage1(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            _payload(["memory/agent/persona.md"]),
            "not json",
        ]

        agent = MemorySearchAgent(mock_client, "system prompt", mem, parse_retries=0)
        results = agent.search("who am i")

        assert [r.path for r in results] == ["memory/agent/persona.md"]

    def test_search_stage2_exception_falls_back_to_stage1(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            _payload(["memory/agent/persona.md"]),
            RuntimeError("stage2 timeout"),
        ]

        agent = MemorySearchAgent(mock_client, "system prompt", mem, parse_retries=0)
        results = agent.search("who am i")

        assert [r.path for r in results] == ["memory/agent/persona.md"]

    def test_search_returns_empty_when_stage1_fails_all_retries(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.return_value = "not json"

        agent = MemorySearchAgent(mock_client, "system prompt", mem, parse_retries=1)
        results = agent.search("anything")

        assert results == []
        assert mock_client.chat.call_count == 2

    def test_search_returns_empty_when_stage1_has_no_candidates(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({"results": []})

        agent = MemorySearchAgent(mock_client, "system prompt", mem)
        results = agent.search("nonexistent topic")

        assert results == []
        assert mock_client.chat.call_count == 1

    def test_search_filters_invalid_and_missing_paths(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        (mem / "valid.md").write_text("valid file", encoding="utf-8")
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            _payload(["memory/valid.md", "/etc/passwd", "memory/missing.md"]),
            _payload(["memory/valid.md"]),
        ]

        agent = MemorySearchAgent(mock_client, "system prompt", mem)
        results = agent.search("test")

        assert [r.path for r in results] == ["memory/valid.md"]

    def test_search_filters_index_paths(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            _payload(["memory/agent/index.md", "memory/agent/persona.md"]),
            _payload(["memory/agent/persona.md"]),
        ]

        agent = MemorySearchAgent(mock_client, "system prompt", mem)
        results = agent.search("agent info")

        assert [r.path for r in results] == ["memory/agent/persona.md"]

    def test_search_restricts_stage2_to_stage1_candidates(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            _payload(["memory/agent/persona.md"]),
            _payload(["memory/people/user-yufeng.md"]),
        ]

        agent = MemorySearchAgent(mock_client, "system prompt", mem)
        results = agent.search("agent info")

        assert results == []

    def test_search_applies_max_results_when_configured(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        (mem / "a.md").write_text("a", encoding="utf-8")
        (mem / "b.md").write_text("b", encoding="utf-8")
        (mem / "c.md").write_text("c", encoding="utf-8")
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            _payload(["memory/a.md", "memory/b.md", "memory/c.md"]),
            _payload(["memory/a.md", "memory/b.md", "memory/c.md"]),
        ]

        agent = MemorySearchAgent(mock_client, "system prompt", mem, max_results=2)
        results = agent.search("all")

        assert [r.path for r in results] == ["memory/a.md", "memory/b.md"]

    def test_search_without_max_results_does_not_truncate(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        (mem / "a.md").write_text("a", encoding="utf-8")
        (mem / "b.md").write_text("b", encoding="utf-8")
        (mem / "c.md").write_text("c", encoding="utf-8")
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            _payload(["memory/a.md", "memory/b.md", "memory/c.md"]),
            _payload(["memory/a.md", "memory/b.md", "memory/c.md"]),
        ]

        agent = MemorySearchAgent(mock_client, "system prompt", mem, max_results=None)
        results = agent.search("all")

        assert [r.path for r in results] == ["memory/a.md", "memory/b.md", "memory/c.md"]

    def test_search_retries_stage1_parse_failure_with_custom_prompt(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            "bad json",
            _payload(["memory/short-term.md"]),
            _payload(["memory/short-term.md"]),
        ]

        agent = MemorySearchAgent(
            mock_client,
            "system prompt",
            mem,
            parse_retries=1,
            parse_retry_prompt="CUSTOM RETRY",
        )
        results = agent.search("recent")

        assert [r.path for r in results] == ["memory/short-term.md"]
        second_call_messages = mock_client.chat.call_args_list[1][0][0]
        assert second_call_messages[-1].content == "CUSTOM RETRY"

    def test_search_retries_stage2_parse_failure_then_succeeds(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            _payload(["memory/short-term.md"]),
            "bad json",
            _payload(["memory/short-term.md"]),
        ]

        agent = MemorySearchAgent(mock_client, "system prompt", mem, parse_retries=1)
        results = agent.search("recent")

        assert [r.path for r in results] == ["memory/short-term.md"]
        assert mock_client.chat.call_count == 3

    def test_build_memory_context_includes_indexes_without_limit(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        agent = MemorySearchAgent(mock_client, "system prompt", mem, context_bytes_limit=None)

        context = agent._build_memory_context()

        assert "Memory Index" in context
        assert "Agent Index" in context
        assert "People Index" in context
        assert "short-term.md" in context

    def test_build_memory_context_skips_large_section_and_continues(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        big_index = mem / "agent" / "index.md"
        big_index.write_text("# Agent Index\n" + ("X" * 5000), encoding="utf-8")
        mock_client = MagicMock()
        agent = MemorySearchAgent(mock_client, "system prompt", mem, context_bytes_limit=400)

        context = agent._build_memory_context()

        assert "Agent Index" not in context
        assert "People Index" in context

    def test_build_memory_context_missing_dir(self, tmp_path: Path):
        mem = tmp_path / "nonexistent"
        mock_client = MagicMock()
        agent = MemorySearchAgent(mock_client, "system prompt", mem)

        context = agent._build_memory_context()
        assert "does not exist" in context


class TestCreateMemorySearch:
    def test_returns_formatted_results(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            _payload(["memory/agent/persona.md", "memory/short-term.md"]),
            _payload(["memory/agent/persona.md", "memory/short-term.md"]),
        ]

        agent = MemorySearchAgent(mock_client, "system prompt", mem)
        tool_fn = create_memory_search(agent)
        output = tool_fn(query="who am I?")

        assert "memory/agent/persona.md" in output
        assert "memory/short-term.md" in output

    def test_returns_message_on_no_results(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({"results": []})

        agent = MemorySearchAgent(mock_client, "system prompt", mem)
        tool_fn = create_memory_search(agent)
        output = tool_fn(query="nonexistent")

        assert "No relevant memory files found" in output

    def test_returns_error_on_empty_query(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()

        agent = MemorySearchAgent(mock_client, "system prompt", mem)
        tool_fn = create_memory_search(agent)
        output = tool_fn(query="")

        assert "Error" in output
        mock_client.chat.assert_not_called()
