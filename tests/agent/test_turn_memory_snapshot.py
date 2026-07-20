from __future__ import annotations

from pathlib import Path

from lincy.agent.turn_runtime import _TurnMemorySnapshot
from lincy.llm.schema import ToolCall


def _memory_edit_call(path: str) -> ToolCall:
    return ToolCall(
        id="tc1",
        name="memory_edit",
        arguments={
            "as_of": "2026-05-17T20:05:00+08:00",
            "turn_id": "turn-1",
            "requests": [
                {
                    "request_id": "r1",
                    "target_path": path,
                    "instruction": "append",
                }
            ],
        },
    )


def test_turn_memory_snapshot_does_not_read_temp_memory_bytes(tmp_path: Path, monkeypatch):
    target = tmp_path / "memory" / "agent" / "temp-memory.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"- [2026-05-17 10:00] existing\n")

    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(self):  # noqa: ANN001
        if self == target:
            raise AssertionError("temp-memory.md snapshot must not read the full file")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    snapshot = _TurnMemorySnapshot(agent_os_dir=tmp_path)
    snapshot.capture_from_tool_call(_memory_edit_call("memory/agent/temp-memory.md"))

    with target.open("ab") as f:
        f.write(b"- [2026-05-17 20:05] appended\n")

    restored = snapshot.rollback()

    assert restored == 1
    with target.open("rb") as f:
        assert f.read() == b"- [2026-05-17 10:00] existing\n"


def test_turn_memory_snapshot_still_restores_regular_memory_file(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "long-term.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"original\n")

    snapshot = _TurnMemorySnapshot(agent_os_dir=tmp_path)
    snapshot.capture_from_tool_call(_memory_edit_call("memory/agent/long-term.md"))

    target.write_bytes(b"changed\n")

    restored = snapshot.rollback()

    assert restored == 1
    assert target.read_bytes() == b"original\n"
