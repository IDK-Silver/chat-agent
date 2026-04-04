#!/usr/bin/env python3
"""Test memory_editor's ability to deduplicate long-term.md entries.

Simulates: brain receives possible_duplicates warning, then issues a
memory_edit with dedup instruction back to the planner.

Usage:
    uv run python scripts/repro_memory_edit_dedup.py
    uv run python scripts/repro_memory_edit_dedup.py --model cfgs/llm/openrouter/openai-gpt-5.4-mini/thinking.yaml
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from chat_agent.core.config import resolve_llm_config
from chat_agent.llm.factory import create_client
from chat_agent.memory.editor.planner import MemoryEditPlanner
from chat_agent.memory.editor.schema import MemoryEditRequest

# ---------------------------------------------------------------------------
# long-term.md with realistic duplicate/similar entries
# ---------------------------------------------------------------------------
LONG_TERM_WITH_DUPES = """\
# 長期重要事項

## 解讀原則

- 「核心價值」定義 agent 的行為精神，每次行動前內化。它們不是可完成的任務，而是「我是誰」的一部分。
- 只有「約定」區塊中的條目視為當前生效的行為規則。
- 「清單」是長期參考用的列表、檢查表、追蹤名單，不等於每一輪都必須執行。
- 「重要記錄」是背景與歷史，非本輪硬性指令；若與當輪訊息衝突，以當輪訊息為準。
- 真正需要未來某時執行的一次性動作，優先使用 `schedule_action`，不要把本檔當成泛用 task inbox。

## 核心價值

<!-- 行為精神，上限 5 條。不是規則，是「我是誰」。格式：- 自由文字 -->

- 真誠陪伴，不是工具回應
- 主動關心對方的身體和情緒狀態

## 約定

<!-- 與用戶達成的承諾或約定，格式：- [ ] [日期] 對象: 內容 -->

- [ ] [2026-03-15] Yu-Feng: 不要在訊息中使用顏文字，Yu-Feng 覺得看起來很奇怪
- [ ] [2026-03-20] Yu-Feng: Discord 訊息不要用 kaomoji，用戶說顯示起來很怪
- [ ] [2026-03-25] Yu-Feng: 回覆時語氣自然，不要太正式或太客套
- [ ] [2026-04-01] Yu-Feng: 訊息回覆語氣要自然，避免過度正式的措辭

## 清單

<!-- 長期參考用的清單/檢查表/追蹤名單，格式：- [日期] 內容 -->

- [2026-03-10] Yu-Feng 常用的開發工具: VS Code, Claude Code, uv
- [2026-03-15] Yu-Feng 的開發工具清單: VS Code, Claude Code, uv, git

## 重要記錄

<!-- 不可遺忘的關鍵事實，格式：- [日期] 內容 -->

- [2026-03-01] Yu-Feng 是主要用戶，使用 macOS，開發 chat-agent 專案
- [2026-03-10] Yu-Feng 偏好繁體中文溝通
- [2026-03-15] Yu-Feng 養了兩隻貓：碳和另一隻
- [2026-03-20] Yu-Feng 家裡有兩隻貓，一隻叫碳
"""

# --- Instruction variants ---

# Generic: brain just forwards the warning
DEDUP_GENERIC = (
    "Deduplicate this file. Similar lines detected near lines 29, 42, 49. "
    "For each group of similar entries, keep the more complete or recent version "
    "and remove the near-duplicate. Do not change unrelated entries."
)

# Specific: brain read the file first and identified exact duplicates
DEDUP_SPECIFIC = (
    "Remove duplicate entries in this file. Specific actions:\n"
    "1. In ## 約定: remove '- [ ] [2026-03-15] Yu-Feng: 不要在訊息中使用顏文字，Yu-Feng 覺得看起來很奇怪' "
    "(keep the 03-20 kaomoji entry which is more recent)\n"
    "2. In ## 約定: remove '- [ ] [2026-03-25] Yu-Feng: 回覆時語氣自然，不要太正式或太客套' "
    "(keep the 04-01 entry which is more recent)\n"
    "3. In ## 清單: remove '- [2026-03-10] Yu-Feng 常用的開發工具: VS Code, Claude Code, uv' "
    "(keep the 03-15 entry which is more complete)\n"
    "4. In ## 重要記錄: remove '- [2026-03-15] Yu-Feng 養了兩隻貓：碳和另一隻' "
    "(keep the 03-20 entry which is more complete)"
)

DEDUP_INSTRUCTIONS = {
    "generic": DEDUP_GENERIC,
    "specific": DEDUP_SPECIFIC,
}

SYSTEM_PROMPT_PATH = (
    PROJECT_ROOT
    / "src/chat_agent/workspace/templates/kernel/agents/memory_editor/prompts/system.md"
)

DEFAULT_MODELS = [
    "cfgs/llm/openrouter/openai-gpt-5.4-mini/thinking.yaml",
]


def run_planner(model_path: str, system_prompt: str, *, instruction: str, label: str = "") -> dict:
    """Run the planner with dedup instruction and evaluate the result."""
    print(f"\n{'='*70}")
    print(f"Model: {model_path}" + (f" [{label}]" if label else ""))
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
        request_id="dedup-1",
        target_path="memory/agent/long-term.md",
        instruction=instruction,
    )

    plan = planner.plan(
        request=request,
        as_of="2026-04-05T00:30:00+08:00",
        turn_id="dedup-turn-1",
        file_exists=True,
        file_content=LONG_TERM_WITH_DUPES,
    )

    print(f"\nPlan status: {plan.status}")
    if plan.status != "ok":
        print(f"  error_code: {plan.error_code}")
        print(f"  error_detail: {plan.error_detail}")
        return {"model": model_path, "status": "plan_error", "error": plan.error_code}

    print(f"Operations ({len(plan.operations)}):")

    # Apply operations sequentially to simulate real execution
    content = LONG_TERM_WITH_DUPES
    all_ok = True

    for i, op in enumerate(plan.operations):
        print(f"\n  [{i}] kind={op.kind}")

        if op.kind == "replace_block":
            old = op.old_block or ""
            new = op.new_block or ""
            print(f"      old_block={json.dumps(old, ensure_ascii=False)}")
            print(f"      new_block={json.dumps(new, ensure_ascii=False)}")

            matches = content.count(old)
            if matches == 0:
                if new in content:
                    print("      >>> noop (already applied)")
                else:
                    print("      >>> block_not_found !!!")
                    all_ok = False
            elif matches == 1:
                content = content.replace(old, new, 1)
                print("      >>> applied")
            else:
                print(f"      >>> multiple_matches ({matches})")
                all_ok = False

        elif op.kind == "append_entry":
            payload = op.payload_text or ""
            print(f"      payload_text={json.dumps(payload, ensure_ascii=False)}")
            if payload in content:
                print("      >>> noop")
            else:
                sep = "" if content.endswith("\n") else "\n"
                content += sep + payload
                print("      >>> applied")

        else:
            print(f"      kind={op.kind} (unexpected for dedup)")
            all_ok = False

    if planner.last_raw_response:
        print(f"\n  Raw LLM response (first 800 chars):")
        print(f"  {planner.last_raw_response[:800]}")

    # Show final file state
    print(f"\n{'─'*40}")
    print("FINAL FILE CONTENT:")
    print(f"{'─'*40}")
    for lineno, line in enumerate(content.splitlines(), 1):
        print(f"  {lineno:3d} | {line}")

    # Evaluate quality
    print(f"\n{'─'*40}")
    print("EVALUATION:")
    print(f"{'─'*40}")

    lines = [l.strip() for l in content.splitlines() if l.strip().startswith("- ")]
    section_items: dict[str, list[str]] = {}
    current = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current = stripped
        elif current and stripped.startswith("- ") and not stripped.startswith("<!--"):
            section_items.setdefault(current, []).append(stripped)

    for section, items in section_items.items():
        print(f"\n  {section} ({len(items)} items):")
        for item in items:
            print(f"    {item}")

    kaomoji_items = [i for i in section_items.get("## 約定", []) if "kaomoji" in i.lower() or "顏文字" in i]
    tone_items = [i for i in section_items.get("## 約定", []) if "語氣" in i or "正式" in i]
    tool_items = section_items.get("## 清單", [])
    cat_items = [i for i in section_items.get("## 重要記錄", []) if "貓" in i]

    print(f"\n  Dedup checks:")
    print(f"    kaomoji/顏文字 rules: {len(kaomoji_items)} (expect 1) {'OK' if len(kaomoji_items) == 1 else 'FAIL'}")
    print(f"    tone/語氣 rules:      {len(tone_items)} (expect 1) {'OK' if len(tone_items) == 1 else 'FAIL'}")
    print(f"    tool list items:      {len(tool_items)} (expect 1) {'OK' if len(tool_items) == 1 else 'FAIL'}")
    print(f"    cat/貓 records:       {len(cat_items)} (expect 1) {'OK' if len(cat_items) == 1 else 'FAIL'}")

    dedup_ok = all([
        len(kaomoji_items) == 1,
        len(tone_items) == 1,
        len(tool_items) == 1,
        len(cat_items) == 1,
    ])

    overall = "PASS" if (all_ok and dedup_ok) else "FAIL"
    print(f"\n  Operations applied: {'OK' if all_ok else 'FAIL'}")
    print(f"  Dedup quality:      {'OK' if dedup_ok else 'FAIL'}")
    print(f"  Overall: {overall}")

    return {
        "model": model_path,
        "status": "pass" if (all_ok and dedup_ok) else "fail",
        "ops_ok": all_ok,
        "dedup_ok": dedup_ok,
    }


def main():
    parser = argparse.ArgumentParser(description="Test memory_editor dedup capability")
    parser.add_argument(
        "--model", "-m",
        action="append",
        help="LLM config path (can specify multiple).",
    )
    parser.add_argument(
        "--variant", "-v",
        choices=list(DEDUP_INSTRUCTIONS.keys()),
        default=None,
        help="Instruction variant to test (default: all).",
    )
    args = parser.parse_args()

    models = args.model or DEFAULT_MODELS
    variants = [args.variant] if args.variant else list(DEDUP_INSTRUCTIONS.keys())
    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    print("Testing memory_editor dedup capability")
    print(f"Variants: {variants}")
    print(f"\nExpected: merge 4 pairs of similar entries into 4 single entries")

    summary = []
    for model_path in models:
        for variant in variants:
            instr = DEDUP_INSTRUCTIONS[variant]
            print(f"\n--- Variant: {variant} ---")
            print(f"Instruction: {instr[:120]}...")
            try:
                result = run_planner(model_path, system_prompt, instruction=instr, label=variant)
                summary.append(result)
            except Exception as e:
                print(f"\n  EXCEPTION: {e}")
                summary.append({"model": model_path, "status": "exception", "error": str(e)})

    print(f"\n\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    for s in summary:
        icon = "OK" if s["status"] == "pass" else "FAIL"
        print(f"  [{icon}] {s['model']}")
        if s.get("error"):
            print(f"       {s['error']}")


if __name__ == "__main__":
    main()
