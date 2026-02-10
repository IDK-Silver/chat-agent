"""Tests for MemorySearchAgent and create_memory_search."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from chat_agent.tools.builtin.memory_search import (
    MemorySearchAgent,
    MemorySearchResult,
    create_memory_search,
)


def _make_memory(tmp_path: Path) -> Path:
    """Create a minimal memory directory with indexes."""
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "short-term.md").write_text("recent events")

    agent_dir = mem / "agent"
    agent_dir.mkdir()
    (agent_dir / "index.md").write_text("# Agent Index\n- persona.md\n- inner-state.md")
    (agent_dir / "persona.md").write_text("persona data")

    people_dir = mem / "people"
    people_dir.mkdir()
    (people_dir / "index.md").write_text("# People Index\n- user-yufeng.md")
    (people_dir / "user-yufeng.md").write_text("user data")

    return mem


class TestMemorySearchAgent:
    def test_search_returns_results(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "results": [
                {"path": "memory/agent/persona.md", "relevance": "Contains persona info"},
            ]
        })

        agent = MemorySearchAgent(mock_client, "system prompt", mem)
        results = agent.search("who am I?")

        assert len(results) == 1
        assert results[0].path == "memory/agent/persona.md"
        assert results[0].relevance == "Contains persona info"

    def test_search_empty_on_no_results(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({"results": []})

        agent = MemorySearchAgent(mock_client, "system prompt", mem)
        results = agent.search("nonexistent topic")

        assert results == []

    def test_search_retries_on_parse_failure(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            "not valid json",
            json.dumps({
                "results": [
                    {"path": "memory/short-term.md", "relevance": "Recent events"},
                ]
            }),
        ]

        agent = MemorySearchAgent(mock_client, "system prompt", mem, parse_retries=1)
        results = agent.search("recent events")

        assert len(results) == 1
        assert mock_client.chat.call_count == 2

    def test_search_returns_empty_after_all_retries_fail(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.return_value = "not json"

        agent = MemorySearchAgent(mock_client, "system prompt", mem, parse_retries=1)
        results = agent.search("anything")

        assert results == []
        assert mock_client.chat.call_count == 2

    def test_search_returns_empty_on_exception(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.side_effect = TimeoutError("Timeout")

        agent = MemorySearchAgent(mock_client, "system prompt", mem)
        results = agent.search("anything")

        assert results == []

    def test_search_caps_at_max_results(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        # Return 10 results, should be capped at 8
        mock_client.chat.return_value = json.dumps({
            "results": [
                {"path": f"memory/file{i}.md", "relevance": f"result {i}"}
                for i in range(10)
            ]
        })

        agent = MemorySearchAgent(mock_client, "system prompt", mem)
        results = agent.search("everything")

        assert len(results) == 8

    def test_search_filters_invalid_paths(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "results": [
                {"path": "memory/valid.md", "relevance": "ok"},
                {"path": "/etc/passwd", "relevance": "bad path"},
                {"path": "invalid.md", "relevance": "no memory prefix"},
            ]
        })

        agent = MemorySearchAgent(mock_client, "system prompt", mem)
        results = agent.search("test")

        assert len(results) == 1
        assert results[0].path == "memory/valid.md"

    def test_search_handles_markdown_code_block(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.return_value = (
            '```json\n{"results": [{"path": "memory/short-term.md", '
            '"relevance": "Recent context"}]}\n```'
        )

        agent = MemorySearchAgent(mock_client, "system prompt", mem)
        results = agent.search("recent")

        assert len(results) == 1

    def test_build_memory_context_includes_indexes(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        agent = MemorySearchAgent(mock_client, "system prompt", mem)

        context = agent._build_memory_context()

        assert "Memory Index" in context
        assert "Agent Index" in context
        assert "People Index" in context
        assert "short-term.md" in context

    def test_build_memory_context_missing_dir(self, tmp_path: Path):
        mem = tmp_path / "nonexistent"
        mock_client = MagicMock()
        agent = MemorySearchAgent(mock_client, "system prompt", mem)

        context = agent._build_memory_context()
        assert "does not exist" in context

    def test_custom_parse_retry_prompt(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            "bad",
            json.dumps({"results": []}),
        ]

        agent = MemorySearchAgent(
            mock_client, "system prompt", mem,
            parse_retries=1,
            parse_retry_prompt="CUSTOM RETRY",
        )
        agent.search("test")

        second_call_messages = mock_client.chat.call_args_list[1][0][0]
        assert second_call_messages[-1].content == "CUSTOM RETRY"

    def test_no_warning_for_intermediate_parse_failure(self, tmp_path: Path, caplog):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            "bad json",
            json.dumps({"results": []}),
        ]

        agent = MemorySearchAgent(mock_client, "system prompt", mem, parse_retries=1)

        with caplog.at_level("WARNING"):
            agent.search("test")

        assert all(
            "Failed to parse memory search" not in rec.message
            for rec in caplog.records
        )


class TestCreateMemorySearch:
    def test_returns_formatted_results(self, tmp_path: Path):
        mem = _make_memory(tmp_path)
        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "results": [
                {"path": "memory/agent/persona.md", "relevance": "Persona info"},
                {"path": "memory/short-term.md", "relevance": "Recent events"},
            ]
        })

        agent = MemorySearchAgent(mock_client, "system prompt", mem)
        tool_fn = create_memory_search(agent)
        output = tool_fn(query="who am I?")

        assert "memory/agent/persona.md" in output
        assert "Persona info" in output
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
