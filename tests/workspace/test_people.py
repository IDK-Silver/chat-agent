from pathlib import Path

from chat_agent.workspace.people import load_people_index, save_people_index


def test_load_people_index_header_only_is_not_legacy(tmp_path: Path) -> None:
    index_path = tmp_path / "memory" / "people" / "index.md"
    save_people_index(index_path, entries=[], legacy=None)

    entries, legacy = load_people_index(index_path)

    assert entries == []
    assert legacy is None
