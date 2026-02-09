"""Tests for memory writer deterministic pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from chat_agent.memory_writer.apply import apply_request
from chat_agent.memory_writer.schema import MemoryEditBatch, MemoryEditRequest
from chat_agent.memory_writer.service import MemoryWriter
from chat_agent.memory_writer.session_log import SessionCommitLog


class _StubClient:
    def __init__(self, responses: list[str]):
        self._responses = responses
        self.calls = 0

    def chat(self, messages):  # noqa: ANN001
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        return "{}"

    def chat_with_tools(self, messages, tools):  # noqa: ANN001
        raise NotImplementedError


def _allowed(base_dir: Path) -> list[str]:
    return [str(base_dir)]


def test_apply_create_if_missing(tmp_path: Path):
    request = MemoryEditRequest(
        request_id="r1",
        kind="create_if_missing",
        target_path="memory/agent/skills/demo.md",
        payload_text="hello",
    )

    first = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)
    second = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    target = tmp_path / "memory" / "agent" / "skills" / "demo.md"
    assert first.status == "applied"
    assert second.status == "noop"
    assert target.read_text() == "hello"


def test_apply_append_entry(tmp_path: Path):
    target = tmp_path / "memory" / "short-term.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("base")

    request = MemoryEditRequest(
        request_id="r1",
        kind="append_entry",
        target_path="memory/short-term.md",
        payload_text="- [2026-02-08 22:00] entry",
    )

    first = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)
    second = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    content = target.read_text()
    assert first.status == "applied"
    assert second.status == "noop"
    assert "- [2026-02-08 22:00] entry" in content


def test_apply_replace_block(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "persona.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Persona: 卉 (HUI)\n\nbody\n", encoding="utf-8")

    request = MemoryEditRequest(
        request_id="r1",
        kind="replace_block",
        target_path="memory/agent/persona.md",
        old_block="# Persona: 卉 (HUI)",
        new_block="# Persona: 澪希 (LING-XI)",
    )

    first = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)
    second = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    assert first.status == "applied"
    assert second.status == "noop"
    content = target.read_text(encoding="utf-8")
    assert "# Persona: 卉 (HUI)" not in content
    assert "# Persona: 澪希 (LING-XI)" in content


def test_apply_replace_block_multiple_matches_requires_replace_all(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "persona.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("卉\n卉\n", encoding="utf-8")

    request = MemoryEditRequest(
        request_id="r1",
        kind="replace_block",
        target_path="memory/agent/persona.md",
        old_block="卉",
        new_block="澪希",
    )
    result = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)
    assert result.status == "error"
    assert result.code == "multiple_matches"

    request_all = MemoryEditRequest(
        request_id="r2",
        kind="replace_block",
        target_path="memory/agent/persona.md",
        old_block="卉",
        new_block="澪希",
        replace_all=True,
    )
    result_all = apply_request(
        request_all,
        allowed_paths=_allowed(tmp_path),
        base_dir=tmp_path,
    )
    assert result_all.status == "applied"
    assert target.read_text(encoding="utf-8") == "澪希\n澪希\n"


def test_apply_toggle_checkbox(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "pending-thoughts.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("- [ ] task one\n")

    request = MemoryEditRequest(
        request_id="r1",
        kind="toggle_checkbox",
        target_path="memory/agent/pending-thoughts.md",
        item_text="task one",
        checked=True,
    )

    first = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)
    second = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    assert first.status == "applied"
    assert second.status == "noop"
    assert target.read_text().startswith("- [x]")


def test_apply_ensure_index_link(tmp_path: Path):
    index = tmp_path / "memory" / "agent" / "skills" / "index.md"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("# Skills\n\n")

    request = MemoryEditRequest(
        request_id="r1",
        kind="ensure_index_link",
        target_path="memory/agent/skills/index.md",
        index_path="memory/agent/skills/index.md",
        link_path="memory/agent/skills/demo.md",
        link_title="Demo",
    )

    first = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)
    second = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    assert first.status == "applied"
    assert second.status == "noop"
    assert "(memory/agent/skills/demo.md)" in index.read_text()


def test_apply_ensure_index_link_normalizes_relative_path(tmp_path: Path):
    index = tmp_path / "memory" / "agent" / "journal" / "index.md"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("# Journal\n\n")

    request = MemoryEditRequest(
        request_id="r1",
        kind="ensure_index_link",
        target_path="memory/agent/journal/index.md",
        index_path="memory/agent/journal/index.md",
        link_path="2026-02-09-night.md",
        link_title="Night",
    )

    result = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)
    assert result.status == "applied"
    assert "(memory/agent/journal/2026-02-09-night.md)" in index.read_text()


def test_apply_ensure_index_link_normalizes_root_relative_path(tmp_path: Path):
    index = tmp_path / "memory" / "agent" / "knowledge" / "index.md"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("# Knowledge\n\n")

    request = MemoryEditRequest(
        request_id="r1",
        kind="ensure_index_link",
        target_path="memory/agent/knowledge/index.md",
        index_path="memory/agent/knowledge/index.md",
        link_path="agent/knowledge/topic.md",
        link_title="Topic",
    )

    result = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)
    assert result.status == "applied"
    assert "(memory/agent/knowledge/topic.md)" in index.read_text()


def test_apply_ensure_index_link_normalizes_absolute_path(tmp_path: Path):
    index = tmp_path / "memory" / "agent" / "skills" / "index.md"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("# Skills\n\n")

    absolute_link = str(tmp_path / "memory" / "agent" / "skills" / "demo.md")
    request = MemoryEditRequest(
        request_id="r1",
        kind="ensure_index_link",
        target_path="memory/agent/skills/index.md",
        index_path="memory/agent/skills/index.md",
        link_path=absolute_link,
        link_title="Demo",
    )

    result = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)
    assert result.status == "applied"
    assert "(memory/agent/skills/demo.md)" in index.read_text()


def test_apply_ensure_index_link_rejects_external_absolute_path(tmp_path: Path):
    index = tmp_path / "memory" / "agent" / "skills" / "index.md"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("# Skills\n\n")

    request = MemoryEditRequest(
        request_id="r1",
        kind="ensure_index_link",
        target_path="memory/agent/skills/index.md",
        index_path="memory/agent/skills/index.md",
        link_path="/tmp/outside.md",
        link_title="Outside",
    )

    result = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)
    assert result.status == "error"
    assert result.code == "link_path_invalid"


def test_apply_rejects_non_memory_path(tmp_path: Path):
    request = MemoryEditRequest(
        request_id="r1",
        kind="append_entry",
        target_path="notes/outside.md",
        payload_text="entry",
    )
    result = apply_request(request, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)
    assert result.status == "error"
    assert result.code == "path_invalid"


def test_memory_writer_rejects_hash_mismatch(tmp_path: Path):
    request = MemoryEditRequest(
        request_id="r1",
        kind="create_if_missing",
        target_path="memory/agent/skills/demo.md",
        payload_text="hello",
    )
    batch = MemoryEditBatch(
        as_of="2026-02-08T22:30:00+08:00",
        turn_id="turn-1",
        requests=[request],
    )

    client = _StubClient(
        [
            json.dumps(
                {
                    "request_id": "r1",
                    "kind": "create_if_missing",
                    "target_path": "memory/agent/skills/demo.md",
                    "payload_hash": "bad-hash",
                    "decision": "apply",
                    "reason": "x",
                }
            )
        ]
    )
    writer = MemoryWriter(
        client,
        "system",
        "retry",
        parse_retries=0,
        max_retries=0,
        commit_log=SessionCommitLog(),
    )

    result = writer.apply_batch(
        batch,
        allowed_paths=_allowed(tmp_path),
        base_dir=tmp_path,
    )

    assert result.status == "failed"
    assert len(result.errors) == 1
    assert result.errors[0].request_id == "r1"


def test_memory_writer_idempotent_replay(tmp_path: Path):
    request = MemoryEditRequest(
        request_id="r1",
        kind="create_if_missing",
        target_path="memory/agent/skills/demo.md",
        payload_text="hello",
    )
    batch = MemoryEditBatch(
        as_of="2026-02-08T22:30:00+08:00",
        turn_id="turn-1",
        requests=[request],
    )

    client = _StubClient(
        [
            json.dumps(
                {
                    "request_id": "r1",
                    "kind": "create_if_missing",
                    "target_path": "memory/agent/skills/demo.md",
                    "payload_hash": request.payload_hash(),
                    "decision": "apply",
                    "reason": "create new file",
                }
            )
        ]
    )
    writer = MemoryWriter(
        client,
        "system",
        "retry",
        parse_retries=0,
        max_retries=0,
        commit_log=SessionCommitLog(),
    )

    first = writer.apply_batch(batch, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)
    second = writer.apply_batch(batch, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    assert first.status == "ok"
    assert second.status == "ok"
    assert second.applied[0].status == "already_applied"
    assert client.calls == 1
