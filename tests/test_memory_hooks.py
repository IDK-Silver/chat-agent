"""Tests for memory.hooks -- rolling buffer auto-archive."""

from datetime import date, timedelta
from pathlib import Path

import pytest

from chat_agent.core.schema import MemoryArchiveConfig
from chat_agent.memory.hooks import (
    ArchiveResult,
    check_and_archive_buffers,
    _parse_short_term_by_date,
    _parse_inner_state_by_date,
)


# -- helpers -------------------------------------------------------------------

def _make_workspace(tmp_path: Path) -> Path:
    """Create minimal workspace directory structure."""
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "agent").mkdir()
    (tmp_path / "memory" / "agent" / "journal").mkdir()
    return tmp_path


def _write(tmp_path: Path, rel_path: str, content: str):
    p = tmp_path / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _today() -> date:
    return date.today()


def _days_ago(n: int) -> date:
    return _today() - timedelta(days=n)


def _build_short_term(entries: dict[date, list[str]]) -> str:
    """Build a short-term.md from {date: [event_lines]}."""
    parts = []
    for d in sorted(entries):
        for event in entries[d]:
            parts.append(f"## [{d.isoformat()} 12:00] {event}\n")
            parts.append(f"- detail for {event}\n")
            parts.append(f"- more detail\n")
    return "".join(parts)


def _build_inner_state(entries: dict[date, int]) -> str:
    """Build inner-state.md from {date: num_entries}."""
    lines = []
    for d in sorted(entries):
        for i in range(entries[d]):
            lines.append(
                f"- [{d.isoformat()} 12:{i:02d}] emotion-{i}: description for {d}\n"
            )
    return "".join(lines)


# -- parser unit tests ---------------------------------------------------------

class TestParseShortTerm:
    def test_basic_split(self):
        content = (
            "## [2026-02-08 12:00] Event A\n"
            "- detail A\n"
            "## [2026-02-09 14:00] Event B\n"
            "- detail B\n"
        )
        result = _parse_short_term_by_date(content)
        assert set(result.keys()) == {date(2026, 2, 8), date(2026, 2, 9)}
        assert "Event A" in result[date(2026, 2, 8)]
        assert "Event B" in result[date(2026, 2, 9)]

    def test_multiple_sections_same_date(self):
        content = (
            "## [2026-02-08 10:00] Morning\n"
            "- morning detail\n"
            "## [2026-02-08 20:00] Evening\n"
            "- evening detail\n"
        )
        result = _parse_short_term_by_date(content)
        assert len(result) == 1
        assert "Morning" in result[date(2026, 2, 8)]
        assert "Evening" in result[date(2026, 2, 8)]

    def test_preamble_discarded(self):
        content = (
            "# Short-term Memory\n"
            "\n"
            "Some preamble text.\n"
            "## [2026-02-10 09:00] First\n"
            "- detail\n"
        )
        result = _parse_short_term_by_date(content)
        assert len(result) == 1
        assert "preamble" not in result[date(2026, 2, 10)]

    def test_empty_content(self):
        assert _parse_short_term_by_date("") == {}


class TestParseInnerState:
    def test_basic_grouping(self):
        content = (
            "- [2026-02-08 12:00] happy: good day\n"
            "- [2026-02-08 13:00] calm: relaxed\n"
            "- [2026-02-09 10:00] alert: morning\n"
        )
        result = _parse_inner_state_by_date(content)
        assert set(result.keys()) == {date(2026, 2, 8), date(2026, 2, 9)}
        assert result[date(2026, 2, 8)].count("\n") == 2
        assert result[date(2026, 2, 9)].count("\n") == 1

    def test_empty_content(self):
        assert _parse_inner_state_by_date("") == {}


# -- integration tests ---------------------------------------------------------

class TestCheckAndArchive:
    def test_skip_when_under_threshold(self, tmp_path: Path):
        wd = _make_workspace(tmp_path)
        _write(wd, "memory/agent/short-term.md", "## [2026-02-10 12:00] Event\n- ok\n")
        config = MemoryArchiveConfig(max_lines=300, retain_days=3)

        result = check_and_archive_buffers(wd, config)

        assert not result.archived
        # Original untouched
        content = (wd / "memory/agent/short-term.md").read_text()
        assert "Event" in content

    def test_skip_when_file_missing(self, tmp_path: Path):
        wd = _make_workspace(tmp_path)
        config = MemoryArchiveConfig(max_lines=50, retain_days=3)

        result = check_and_archive_buffers(wd, config)
        assert not result.archived

    def test_archive_short_term_by_date(self, tmp_path: Path):
        wd = _make_workspace(tmp_path)
        today = _today()
        entries = {
            _days_ago(5): ["old_event_1", "old_event_2"] * 20,
            _days_ago(4): ["old_event_3"] * 20,
            _days_ago(2): ["recent_event_1"] * 10,
            today: ["today_event"] * 10,
        }
        _write(wd, "memory/agent/short-term.md", _build_short_term(entries))
        config = MemoryArchiveConfig(max_lines=50, retain_days=3)

        result = check_and_archive_buffers(wd, config)

        assert result.archived
        assert result.total_lines > 0

        # Original should only contain recent + today
        remaining = (wd / "memory/agent/short-term.md").read_text()
        assert "old_event_1" not in remaining
        assert "old_event_3" not in remaining
        assert "recent_event_1" in remaining
        assert "today_event" in remaining

        # Archive files created
        archive_dir = wd / "memory/agent/journal/short-term"
        assert archive_dir.is_dir()
        assert (archive_dir / f"{_days_ago(5).isoformat()}.md").is_file()
        assert (archive_dir / f"{_days_ago(4).isoformat()}.md").is_file()
        assert not (archive_dir / f"{_days_ago(2).isoformat()}.md").exists()

        # Index updated
        index = (archive_dir / "index.md").read_text()
        assert _days_ago(5).isoformat() in index
        assert _days_ago(4).isoformat() in index

    def test_archive_inner_state_by_date(self, tmp_path: Path):
        wd = _make_workspace(tmp_path)
        entries = {
            _days_ago(5): 50,
            _days_ago(4): 50,
            _days_ago(2): 30,
            _today(): 20,
        }
        _write(wd, "memory/agent/inner-state.md", _build_inner_state(entries))
        config = MemoryArchiveConfig(max_lines=50, retain_days=3)

        result = check_and_archive_buffers(wd, config)

        assert result.archived
        remaining = (wd / "memory/agent/inner-state.md").read_text()
        assert _days_ago(5).isoformat() not in remaining
        assert _days_ago(2).isoformat() in remaining

        archive_dir = wd / "memory/agent/journal/inner-state"
        assert (archive_dir / f"{_days_ago(5).isoformat()}.md").is_file()

    def test_archive_appends_to_existing(self, tmp_path: Path):
        wd = _make_workspace(tmp_path)
        old_date = _days_ago(5)

        # Pre-existing archive file
        archive_dir = wd / "memory/agent/journal/short-term"
        archive_dir.mkdir(parents=True)
        existing_file = archive_dir / f"{old_date.isoformat()}.md"
        existing_file.write_text("## Previously archived\n", encoding="utf-8")

        entries = {old_date: ["new_old_event"] * 50, _today(): ["today"] * 10}
        _write(wd, "memory/agent/short-term.md", _build_short_term(entries))
        config = MemoryArchiveConfig(max_lines=50, retain_days=3)

        check_and_archive_buffers(wd, config)

        content = existing_file.read_text()
        assert "Previously archived" in content
        assert "new_old_event" in content

    def test_idempotent_rerun(self, tmp_path: Path):
        wd = _make_workspace(tmp_path)
        entries = {
            _days_ago(5): 50,
            _today(): 20,
        }
        _write(wd, "memory/agent/inner-state.md", _build_inner_state(entries))
        config = MemoryArchiveConfig(max_lines=50, retain_days=3)

        # First run
        r1 = check_and_archive_buffers(wd, config)
        assert r1.archived

        # Second run: file is now small, should skip
        r2 = check_and_archive_buffers(wd, config)
        assert not r2.archived

    def test_no_old_entries_to_archive(self, tmp_path: Path):
        """All entries within retain window -- no archival despite line count."""
        wd = _make_workspace(tmp_path)
        entries = {
            _days_ago(2): ["event"] * 60,
            _days_ago(1): ["event"] * 60,
            _today(): ["event"] * 60,
        }
        _write(wd, "memory/agent/short-term.md", _build_short_term(entries))
        config = MemoryArchiveConfig(max_lines=50, retain_days=3)

        result = check_and_archive_buffers(wd, config)
        assert not result.archived

    def test_archive_result_summary(self, tmp_path: Path):
        wd = _make_workspace(tmp_path)
        entries = {_days_ago(5): 100, _today(): 10}
        _write(wd, "memory/agent/inner-state.md", _build_inner_state(entries))
        config = MemoryArchiveConfig(max_lines=50, retain_days=3)

        result = check_and_archive_buffers(wd, config)
        assert "archived" in result.summary
        assert _days_ago(5).isoformat() in result.summary
