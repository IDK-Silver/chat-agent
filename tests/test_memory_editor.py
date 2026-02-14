"""Tests for memory editor v2 (instruction -> planned operations)."""

from __future__ import annotations

from pathlib import Path
from threading import Barrier, BrokenBarrierError

from chat_agent.memory.editor.apply import apply_operation
from chat_agent.memory.editor.schema import (
    MemoryEditBatch,
    MemoryEditOperation,
    MemoryEditPlan,
    MemoryEditRequest,
)
from chat_agent.memory.editor.service import MemoryEditor
from chat_agent.memory.editor.session_log import SessionCommitLog


def _allowed(base_dir: Path) -> list[str]:
    return [str(base_dir)]


class _StaticPlanner:
    """Planner stub returning predefined plans by request_id."""

    def __init__(self, plans: dict[str, MemoryEditPlan]):
        self._plans = plans

    def plan(self, *, request, as_of, turn_id, file_exists, file_content):  # noqa: ANN001,ARG002
        return self._plans[request.request_id]


class _BarrierPlanner:
    """Planner stub that requires concurrent calls to proceed."""

    def __init__(self, plans: dict[str, MemoryEditPlan], parties: int):
        self._plans = plans
        self._barrier = Barrier(parties)

    def plan(self, *, request, as_of, turn_id, file_exists, file_content):  # noqa: ANN001,ARG002
        try:
            self._barrier.wait(timeout=1.0)
        except BrokenBarrierError as e:
            raise AssertionError(
                "expected planner calls to run in parallel across target files"
            ) from e
        return self._plans[request.request_id]


class _SameFileOrderPlanner:
    """Planner stub asserting same-file requests observe sequential state."""

    def plan(self, *, request, as_of, turn_id, file_exists, file_content):  # noqa: ANN001,ARG002
        if request.request_id == "r1":
            assert file_exists is False
            return MemoryEditPlan(
                status="ok",
                operations=[MemoryEditOperation(kind="create_if_missing", payload_text="# notes")],
            )
        if request.request_id == "r2":
            assert file_exists is True
            assert "# notes" in file_content
            return MemoryEditPlan(
                status="ok",
                operations=[MemoryEditOperation(kind="append_entry", payload_text="- second")],
            )
        raise AssertionError(f"unexpected request_id: {request.request_id}")


def test_apply_delete_file_removes_existing(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "knowledge" / "old-topic.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Old Topic\n", encoding="utf-8")

    operation = MemoryEditOperation(kind="delete_file")
    result = apply_operation(target, operation, base_dir=tmp_path)
    assert result.status == "applied"
    assert not target.exists()


def test_apply_delete_file_noop_when_missing(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "knowledge" / "nonexistent.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    operation = MemoryEditOperation(kind="delete_file")
    result = apply_operation(target, operation, base_dir=tmp_path)
    assert result.status == "noop"


def test_apply_delete_file_rejects_index_md(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "knowledge" / "index.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Index\n", encoding="utf-8")

    operation = MemoryEditOperation(kind="delete_file")
    result = apply_operation(target, operation, base_dir=tmp_path)
    assert result.status == "error"
    assert result.code == "delete_index_forbidden"
    assert target.exists()


def test_apply_delete_file_rejects_directory(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "knowledge"
    target.mkdir(parents=True, exist_ok=True)

    operation = MemoryEditOperation(kind="delete_file")
    result = apply_operation(target, operation, base_dir=tmp_path)
    assert result.status == "error"
    assert result.code == "not_a_file"


def test_apply_toggle_checkbox_apply_all_matches(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "pending-thoughts.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("- [ ] task one\n- [ ] task one\n", encoding="utf-8")

    operation = MemoryEditOperation(
        kind="toggle_checkbox",
        item_text="task one",
        checked=True,
        apply_all_matches=True,
    )
    result = apply_operation(target, operation, base_dir=tmp_path)
    assert result.status == "applied"
    content = target.read_text(encoding="utf-8")
    assert content.count("- [x] task one") == 2


def test_apply_prune_checked_checkboxes(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "pending-thoughts.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "- [x] done one\n- [ ] todo one\n- [X] done two\n",
        encoding="utf-8",
    )

    operation = MemoryEditOperation(kind="prune_checked_checkboxes")
    result = apply_operation(target, operation, base_dir=tmp_path)
    assert result.status == "applied"
    content = target.read_text(encoding="utf-8")
    assert "- [x] done one" not in content
    assert "- [X] done two" not in content
    assert "- [ ] todo one" in content


def test_apply_toggle_checkbox_rest_reminder_regression(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "pending-thoughts.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# 2026-02-10 待分享念頭\n\n"
        "## 生活關懷\n"
        "- [ ] **休息提醒**: 如果昨晚太晚睡，提醒他今天下午找時間補眠。\n",
        encoding="utf-8",
    )

    operation = MemoryEditOperation(
        kind="toggle_checkbox",
        item_text="**休息提醒**: 如果昨晚太晚睡，提醒他今天下午找時間補眠。",
        checked=True,
        apply_all_matches=True,
    )
    result = apply_operation(target, operation, base_dir=tmp_path)
    assert result.status == "applied"
    content = target.read_text(encoding="utf-8")
    assert "- [x] **休息提醒**: 如果昨晚太晚睡，提醒他今天下午找時間補眠。" in content


def test_memory_editor_applies_instruction_plan(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "short-term.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# short-term\n", encoding="utf-8")

    request = MemoryEditRequest(
        request_id="r1",
        target_path="memory/agent/short-term.md",
        instruction="追加今天摘要",
    )
    plan = MemoryEditPlan(
        status="ok",
        operations=[
            MemoryEditOperation(
                kind="append_entry",
                payload_text="- [2026-02-11 00:46] append",
            )
        ],
    )
    batch = MemoryEditBatch(
        as_of="2026-02-11T00:46:32+08:00",
        turn_id="turn-1",
        requests=[request],
    )

    editor = MemoryEditor(
        commit_log=SessionCommitLog(),
        planner=_StaticPlanner({"r1": plan}),
    )
    result = editor.apply_batch(batch, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    assert result.status == "ok"
    assert result.applied[0].status == "applied"
    assert "- [2026-02-11 00:46] append" in target.read_text(encoding="utf-8")


def test_memory_editor_idempotent_replay_with_same_planned_ops(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "short-term.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# short-term\n", encoding="utf-8")

    request = MemoryEditRequest(
        request_id="r1",
        target_path="memory/agent/short-term.md",
        instruction="追加今天摘要",
    )
    plan = MemoryEditPlan(
        status="ok",
        operations=[
            MemoryEditOperation(
                kind="append_entry",
                payload_text="- [2026-02-11 00:46] append",
            )
        ],
    )
    batch = MemoryEditBatch(
        as_of="2026-02-11T00:46:32+08:00",
        turn_id="turn-1",
        requests=[request],
    )

    editor = MemoryEditor(
        commit_log=SessionCommitLog(),
        planner=_StaticPlanner({"r1": plan}),
    )
    first = editor.apply_batch(batch, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)
    second = editor.apply_batch(batch, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    assert first.status == "ok"
    assert first.applied[0].status == "applied"
    assert second.status == "ok"
    assert second.applied[0].status == "already_applied"


def test_memory_editor_rolls_back_request_on_operation_failure(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "short-term.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# short-term\n", encoding="utf-8")

    request = MemoryEditRequest(
        request_id="r1",
        target_path="memory/agent/short-term.md",
        instruction="先加一行再做錯誤替換",
    )
    failing_plan = MemoryEditPlan(
        status="ok",
        operations=[
            MemoryEditOperation(
                kind="append_entry",
                payload_text="- [2026-02-11 00:46] temp",
            ),
            MemoryEditOperation(
                kind="replace_block",
                old_block="does-not-exist",
                new_block="replacement",
            ),
        ],
    )
    batch = MemoryEditBatch(
        as_of="2026-02-11T00:46:32+08:00",
        turn_id="turn-1",
        requests=[request],
    )

    editor = MemoryEditor(
        commit_log=SessionCommitLog(),
        planner=_StaticPlanner({"r1": failing_plan}),
    )
    result = editor.apply_batch(batch, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    assert result.status == "failed"
    assert result.errors[0].code == "block_not_found"
    # request-level atomicity: appended temp line must be rolled back
    assert target.read_text(encoding="utf-8") == "# short-term\n"


def test_memory_editor_returns_instruction_not_actionable_error(tmp_path: Path):
    request = MemoryEditRequest(
        request_id="r1",
        target_path="memory/agent/pending-thoughts.md",
        instruction="這句話沒有可執行的編輯語意",
    )
    plan = MemoryEditPlan(
        status="error",
        error_code="instruction_not_actionable",
        error_detail="planner cannot map instruction to operations",
    )
    batch = MemoryEditBatch(
        as_of="2026-02-11T00:46:32+08:00",
        turn_id="turn-1",
        requests=[request],
    )

    editor = MemoryEditor(
        commit_log=SessionCommitLog(),
        planner=_StaticPlanner({"r1": plan}),
    )
    result = editor.apply_batch(batch, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    assert result.status == "failed"
    assert result.errors[0].code == "instruction_not_actionable"


def test_memory_editor_parallelizes_different_target_files(tmp_path: Path):
    req1 = MemoryEditRequest(
        request_id="r1",
        target_path="memory/agent/a.md",
        instruction="create file a",
    )
    req2 = MemoryEditRequest(
        request_id="r2",
        target_path="memory/agent/b.md",
        instruction="create file b",
    )
    plans = {
        "r1": MemoryEditPlan(
            status="ok",
            operations=[MemoryEditOperation(kind="create_if_missing", payload_text="# a")],
        ),
        "r2": MemoryEditPlan(
            status="ok",
            operations=[MemoryEditOperation(kind="create_if_missing", payload_text="# b")],
        ),
    }
    batch = MemoryEditBatch(
        as_of="2026-02-11T00:46:32+08:00",
        turn_id="turn-1",
        requests=[req1, req2],
    )

    editor = MemoryEditor(
        commit_log=SessionCommitLog(),
        planner=_BarrierPlanner(plans, parties=2),
    )
    result = editor.apply_batch(batch, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    assert result.status == "ok"
    assert [item.request_id for item in result.applied] == ["r1", "r2"]
    assert (tmp_path / "memory" / "agent" / "a.md").read_text(encoding="utf-8") == "# a"
    assert (tmp_path / "memory" / "agent" / "b.md").read_text(encoding="utf-8") == "# b"


def test_memory_editor_same_file_requests_stay_sequential(tmp_path: Path):
    req1 = MemoryEditRequest(
        request_id="r1",
        target_path="memory/agent/notes.md",
        instruction="create notes",
    )
    req2 = MemoryEditRequest(
        request_id="r2",
        target_path="memory/agent/notes.md",
        instruction="append notes",
    )
    batch = MemoryEditBatch(
        as_of="2026-02-11T00:46:32+08:00",
        turn_id="turn-1",
        requests=[req1, req2],
    )

    editor = MemoryEditor(
        commit_log=SessionCommitLog(),
        planner=_SameFileOrderPlanner(),
    )
    result = editor.apply_batch(batch, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    assert result.status == "ok"
    assert [item.request_id for item in result.applied] == ["r1", "r2"]
    content = (tmp_path / "memory" / "agent" / "notes.md").read_text(encoding="utf-8")
    assert "# notes" in content
    assert "- second" in content


def test_memory_editor_delete_file_via_service(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "knowledge" / "old.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# old\n", encoding="utf-8")

    request = MemoryEditRequest(
        request_id="r1",
        target_path="memory/agent/knowledge/old.md",
        instruction="delete this file",
    )
    plan = MemoryEditPlan(
        status="ok",
        operations=[MemoryEditOperation(kind="delete_file")],
    )
    batch = MemoryEditBatch(
        as_of="2026-02-12T10:00:00+08:00",
        turn_id="turn-1",
        requests=[request],
    )

    editor = MemoryEditor(
        commit_log=SessionCommitLog(),
        planner=_StaticPlanner({"r1": plan}),
    )
    result = editor.apply_batch(batch, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    assert result.status == "ok"
    assert result.applied[0].status == "applied"
    assert not target.exists()


def test_memory_editor_delete_file_rollback(tmp_path: Path):
    """Delete followed by a failing operation should restore the deleted file."""
    target = tmp_path / "memory" / "agent" / "knowledge" / "precious.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    original = "# precious data\n"
    target.write_text(original, encoding="utf-8")

    request = MemoryEditRequest(
        request_id="r1",
        target_path="memory/agent/knowledge/precious.md",
        instruction="delete then fail",
    )
    plan = MemoryEditPlan(
        status="ok",
        operations=[
            MemoryEditOperation(kind="delete_file"),
            MemoryEditOperation(
                kind="replace_block",
                old_block="impossible",
                new_block="replacement",
            ),
        ],
    )
    batch = MemoryEditBatch(
        as_of="2026-02-12T10:00:00+08:00",
        turn_id="turn-1",
        requests=[request],
    )

    editor = MemoryEditor(
        commit_log=SessionCommitLog(),
        planner=_StaticPlanner({"r1": plan}),
    )
    result = editor.apply_batch(batch, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    assert result.status == "failed"
    # Rollback should restore the file with original content.
    assert target.exists()
    assert target.read_text(encoding="utf-8") == original


def test_memory_editor_delete_file_idempotent_replay(tmp_path: Path):
    """Second apply_batch for delete_file returns already_applied."""
    target = tmp_path / "memory" / "agent" / "knowledge" / "temp.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# temp\n", encoding="utf-8")

    request = MemoryEditRequest(
        request_id="r1",
        target_path="memory/agent/knowledge/temp.md",
        instruction="delete temp file",
    )
    plan = MemoryEditPlan(
        status="ok",
        operations=[MemoryEditOperation(kind="delete_file")],
    )
    batch = MemoryEditBatch(
        as_of="2026-02-12T10:00:00+08:00",
        turn_id="turn-1",
        requests=[request],
    )

    editor = MemoryEditor(
        commit_log=SessionCommitLog(),
        planner=_StaticPlanner({"r1": plan}),
    )
    first = editor.apply_batch(batch, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)
    second = editor.apply_batch(batch, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    assert first.status == "ok"
    assert first.applied[0].status == "applied"
    assert second.status == "ok"
    assert second.applied[0].status == "already_applied"


# --- overwrite tests ---


def test_apply_overwrite_creates_new_file(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "knowledge" / "new-topic.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    operation = MemoryEditOperation(kind="overwrite", payload_text="# New Topic\n")
    result = apply_operation(target, operation, base_dir=tmp_path)
    assert result.status == "applied"
    assert target.read_text(encoding="utf-8") == "# New Topic\n"


def test_apply_overwrite_replaces_existing(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "knowledge" / "topic.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Old Content\nold line\n", encoding="utf-8")

    operation = MemoryEditOperation(kind="overwrite", payload_text="# New Content\nnew line\n")
    result = apply_operation(target, operation, base_dir=tmp_path)
    assert result.status == "applied"
    assert target.read_text(encoding="utf-8") == "# New Content\nnew line\n"


def test_apply_overwrite_noop_identical(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "knowledge" / "topic.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    content = "# Same Content\n"
    target.write_text(content, encoding="utf-8")

    operation = MemoryEditOperation(kind="overwrite", payload_text=content)
    result = apply_operation(target, operation, base_dir=tmp_path)
    assert result.status == "noop"


def test_apply_overwrite_empty_file(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "knowledge" / "empty.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")

    operation = MemoryEditOperation(kind="overwrite", payload_text="# Filled\n")
    result = apply_operation(target, operation, base_dir=tmp_path)
    assert result.status == "applied"
    assert target.read_text(encoding="utf-8") == "# Filled\n"


def test_memory_editor_overwrite_via_service(tmp_path: Path):
    target = tmp_path / "memory" / "agent" / "knowledge" / "topic.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Old\n", encoding="utf-8")

    request = MemoryEditRequest(
        request_id="r1",
        target_path="memory/agent/knowledge/topic.md",
        instruction="overwrite entire file",
    )
    plan = MemoryEditPlan(
        status="ok",
        operations=[MemoryEditOperation(kind="overwrite", payload_text="# Replaced\nnew content\n")],
    )
    batch = MemoryEditBatch(
        as_of="2026-02-14T12:00:00+08:00",
        turn_id="turn-1",
        requests=[request],
    )

    editor = MemoryEditor(
        commit_log=SessionCommitLog(),
        planner=_StaticPlanner({"r1": plan}),
    )
    result = editor.apply_batch(batch, allowed_paths=_allowed(tmp_path), base_dir=tmp_path)

    assert result.status == "ok"
    assert result.applied[0].status == "applied"
    assert target.read_text(encoding="utf-8") == "# Replaced\nnew content\n"
