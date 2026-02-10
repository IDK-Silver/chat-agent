"""Tests for memory editor v2 (instruction -> planned operations)."""

from __future__ import annotations

from pathlib import Path

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
    target = tmp_path / "memory" / "short-term.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# short-term\n", encoding="utf-8")

    request = MemoryEditRequest(
        request_id="r1",
        target_path="memory/short-term.md",
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
    target = tmp_path / "memory" / "short-term.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# short-term\n", encoding="utf-8")

    request = MemoryEditRequest(
        request_id="r1",
        target_path="memory/short-term.md",
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
    target = tmp_path / "memory" / "short-term.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# short-term\n", encoding="utf-8")

    request = MemoryEditRequest(
        request_id="r1",
        target_path="memory/short-term.md",
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
