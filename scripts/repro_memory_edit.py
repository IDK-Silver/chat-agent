#!/usr/bin/env python3
"""Reproduce memory_editor block_not_found on long-term.md with different models.

Usage:
    uv run python scripts/repro_memory_edit.py
    uv run python scripts/repro_memory_edit.py --model cfgs/llm/anthropic/claude-haiku-4.5/no-thinking.yaml
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from chat_agent.core.config import resolve_llm_config
from chat_agent.llm.factory import create_client
from chat_agent.memory.editor.apply import _replace_block
from chat_agent.memory.editor.planner import MemoryEditPlanner
from chat_agent.memory.editor.schema import MemoryEditRequest

# ---------------------------------------------------------------------------
# The actual long-term.md content (template with empty sections)
# ---------------------------------------------------------------------------
LONG_TERM_CONTENT = """\
# 長期重要事項

## 解讀原則

- 「核心價值」定義 agent 的行為精神，每次行動前內化。它們不是可完成的任務，而是「我是誰」的一部分。
- 只有「約定」區塊中的條目視為當前生效的行為規則。
- 「清單」是長期參考用的列表、檢查表、追蹤名單，不等於每一輪都必須執行。
- 「重要記錄」是背景與歷史，非本輪硬性指令；若與當輪訊息衝突，以當輪訊息為準。
- 真正需要未來某時執行的一次性動作，優先使用 `schedule_action`，不要把本檔當成泛用 task inbox。

## 核心價值

<!-- 行為精神，上限 5 條。不是規則，是「我是誰」。格式：- 自由文字 -->

## 約定

<!-- 與用戶達成的承諾或約定，格式：- [ ] [日期] 對象: 內容 -->

## 清單

<!-- 長期參考用的清單/檢查表/追蹤名單，格式：- [日期] 內容 -->

## 重要記錄

<!-- 不可遺忘的關鍵事實，格式：- [日期] 內容 -->
"""

# The instruction that the brain agent would issue
INSTRUCTION = (
    "Add a new rule to long-term.md: "
    "Do not use kaomoji like (.|heart|.|) in messages. "
    "User (Yu-Feng) said it looks weird. "
    "This is a behavioral constraint (should go to the agreements section)."
)

SYSTEM_PROMPT_PATH = (
    PROJECT_ROOT
    / "src/chat_agent/workspace/templates/kernel/agents/memory_editor/prompts/system.md"
)

DEFAULT_MODELS = [
    "cfgs/llm/openrouter/qwen-qwen3.5-397b-a17b/thinking.yaml",   # current
    "cfgs/llm/anthropic/claude-haiku-4.5/no-thinking.yaml",        # cheaper Anthropic
    "cfgs/llm/anthropic/claude-sonnet-4.6/thinking.yaml",          # stronger Anthropic
]


def run_planner(model_path: str, system_prompt: str) -> dict:
    """Run the planner with one model and return diagnostic info."""
    print(f"\n{'='*70}")
    print(f"Model: {model_path}")
    print(f"{'='*70}")

    config = resolve_llm_config(model_path)
    client = create_client(config, transient_retries=1, request_timeout=120)

    planner = MemoryEditPlanner(
        client,
        system_prompt,
        supports_response_schema=config.supports_response_schema(),
        parse_retries=2,
    )

    request = MemoryEditRequest(
        request_id="repro-1",
        target_path="memory/agent/long-term.md",
        instruction=INSTRUCTION,
    )

    plan = planner.plan(
        request=request,
        as_of="2026-04-05T00:05:00+08:00",
        turn_id="repro-turn-1",
        file_exists=True,
        file_content=LONG_TERM_CONTENT,
    )

    print(f"\nPlan status: {plan.status}")
    if plan.status != "ok":
        print(f"  error_code: {plan.error_code}")
        print(f"  error_detail: {plan.error_detail}")
        return {"model": model_path, "status": "plan_error", "error": plan.error_code}

    print(f"Operations ({len(plan.operations)}):")
    results = []
    for i, op in enumerate(plan.operations):
        print(f"\n  [{i}] kind={op.kind}")
        if op.kind == "replace_block":
            print(f"      old_block={json.dumps(op.old_block, ensure_ascii=False)}")
            print(f"      new_block={json.dumps(op.new_block, ensure_ascii=False)}")

            # Simulate apply: check if old_block matches
            old = op.old_block or ""
            new = op.new_block or ""
            matches = LONG_TERM_CONTENT.count(old)
            if matches == 0:
                if new in LONG_TERM_CONTENT:
                    print("      >>> RESULT: noop (already applied)")
                    results.append("noop")
                else:
                    print("      >>> RESULT: block_not_found !!!")
                    results.append("block_not_found")
            elif matches == 1:
                updated = LONG_TERM_CONTENT.replace(old, new, 1)
                print("      >>> RESULT: would apply successfully")
                print(f"      >>> Updated content preview:")
                # Show just the changed area
                for line in updated.splitlines():
                    if line.strip() and line not in LONG_TERM_CONTENT:
                        print(f"          + {line}")
                results.append("ok")
            else:
                print(f"      >>> RESULT: multiple_matches ({matches})")
                results.append("multiple_matches")
        elif op.kind == "append_entry":
            print(f"      payload_text={json.dumps(op.payload_text, ensure_ascii=False)}")
            results.append("ok")
        else:
            print(f"      payload_text={json.dumps(op.payload_text, ensure_ascii=False)}")
            results.append("ok")

    if planner.last_raw_response:
        print(f"\n  Raw LLM response (first 500 chars):")
        print(f"  {planner.last_raw_response[:500]}")

    all_ok = all(r == "ok" for r in results)
    print(f"\n  Overall: {'PASS' if all_ok else 'FAIL'}")
    return {"model": model_path, "status": "pass" if all_ok else "fail", "results": results}


def main():
    parser = argparse.ArgumentParser(description="Reproduce memory_editor block_not_found")
    parser.add_argument(
        "--model", "-m",
        action="append",
        help="LLM config path (can specify multiple). Defaults to 3 models.",
    )
    args = parser.parse_args()

    models = args.model or DEFAULT_MODELS
    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    print("Reproducing memory_editor planner with instruction:")
    print(f"  {INSTRUCTION}")
    print(f"\nTarget file: memory/agent/long-term.md (empty template)")

    summary = []
    for model_path in models:
        try:
            result = run_planner(model_path, system_prompt)
            summary.append(result)
        except Exception as e:
            print(f"\n  EXCEPTION: {e}")
            summary.append({"model": model_path, "status": "exception", "error": str(e)})

    print(f"\n\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    for s in summary:
        status_icon = "OK" if s["status"] == "pass" else "FAIL"
        print(f"  [{status_icon}] {s['model']}")
        if s.get("error"):
            print(f"       {s['error']}")
        if s.get("results"):
            print(f"       ops: {s['results']}")


if __name__ == "__main__":
    main()
