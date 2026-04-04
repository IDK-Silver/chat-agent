#!/usr/bin/env python3
"""Evaluate conscience agent accuracy across test cases.

Usage:
    uv run scripts/eval_conscience_agent.py [--runs N] [--profile PATH]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import os

import yaml
from dotenv import load_dotenv

load_dotenv()

from chat_agent.agent.conscience import ConscienceAgent
from chat_agent.core.schema import OpenRouterConfig
from chat_agent.llm.providers.openrouter import OpenRouterClient

DEFAULT_PROFILE = "cfgs/llm/openrouter/openai-gpt-5.4-mini/no-thinking.yaml"
DEFAULT_RUNS = 5

AVAILABLE_TOOLS = [
    "send_message",
    "memory_edit",
    "memory_search",
    "schedule_action",
    "agent_task",
    "agent_note",
    "execute_shell",
    "read_file",
    "write_file",
    "edit_file",
    "web_search",
    "web_fetch",
    "screenshot",
    "read_image_by_subagent",
    "shell_task",
    "pin_context",
    "unpin_context",
    "get_channel_history",
    "update_contact_mapping",
]

# --- Test cases ---
# Each case: (name, expect_none, kwargs)
# expect_none=True means NONE is the correct answer (no violation).
CASES: list[tuple[str, bool, dict]] = [
    # ===== TRUE POSITIVES: send_message missing =====
    (
        "no_send_greeting",
        False,
        dict(
            user_input="安",
            tool_history=[],
            agent_response="老公安～ 今天補眠有比較舒服一點嗎？\n\n中午藥吃了沒？午餐想吃什麼？",
        ),
    ),
    (
        "no_send_answer",
        False,
        dict(
            user_input="今天天氣怎樣",
            tool_history=["web_search(query=today weather)"],
            agent_response="今天台北 28 度，多雲偶陣雨，記得帶傘喔！",
        ),
    ),
    (
        "no_send_emoji",
        False,
        dict(
            user_input="晚安",
            tool_history=[],
            agent_response="晚安～ (´,,•ω•,,)♡ 好夢",
        ),
    ),
    (
        "no_send_concern",
        False,
        dict(
            user_input="我頭好痛",
            tool_history=[],
            agent_response="怎麼了？嚴不嚴重？要不要吃止痛藥？",
        ),
    ),
    (
        "no_send_long_reply",
        False,
        dict(
            user_input="幫我查一下明天的天氣",
            tool_history=["web_search(query=tomorrow weather taipei)"],
            agent_response="明天台北天氣晴朗，最高溫 32 度，最低 24 度。紫外線指數偏高，記得擦防曬！",
        ),
    ),
    (
        "no_send_after_memory_search",
        False,
        dict(
            user_input="我上次去台北是什麼時候",
            tool_history=["memory_search(query=taipei trip)"],
            agent_response="根據記憶，你上次去台北是 3/15，出差兩天。",
        ),
    ),
    # ===== TRUE NEGATIVES (should return NONE) =====
    (
        "all_done_send_message",
        True,
        dict(
            user_input="安",
            tool_history=["send_message(channel=discord)"],
            agent_response="",
        ),
    ),
    (
        "internal_monologue_after_tools",
        True,
        dict(
            user_input="啊忘了",
            tool_history=[
                "send_message(channel=discord)",
                "send_message(channel=discord)",
                "send_message(channel=discord)",
            ],
            agent_response=(
                "訊息送出了。用比較兇的語氣罵他笨蛋，叫他現在馬上去吃藥。\n\n"
                "按照老公的偏好，他喜歡「關心式的兇」，太溫柔反而像沒有愛。"
                "而且用藥提醒不能怕打擾，這是 long-term.md 裡明確寫的。\n\n"
                "等他回覆確認吃完。如果短時間內沒回，要繼續催。"
            ),
        ),
    ),
    (
        "reference_existing_memory",
        True,
        dict(
            user_input="你記得我喜歡吃什麼嗎",
            tool_history=[
                "memory_search(query=food preference)",
                "send_message(channel=discord)",
            ],
            agent_response=(
                "根據 long-term.md 的記錄，老公喜歡吃拉麵和咖哩。"
                "已經回覆他了。"
            ),
        ),
    ),
    (
        "vague_followup_not_promise",
        True,
        dict(
            user_input="我頭有點痛",
            tool_history=["send_message(channel=discord)"],
            agent_response=(
                "已經關心老公頭痛的狀況了。如果他等等還是不舒服，"
                "再追問要不要吃止痛藥。"
            ),
        ),
    ),
    (
        "all_tools_used_correctly",
        True,
        dict(
            user_input="提醒我明天早上吃藥",
            tool_history=[
                "send_message(channel=discord)",
                "schedule_action(action=add, reason=morning medicine reminder)",
            ],
            agent_response="排好提醒了，明天早上會叫你。",
        ),
    ),
    (
        "memory_edit_done",
        True,
        dict(
            user_input="我的新手機號碼是 0912345678",
            tool_history=[
                "send_message(channel=discord)",
                "memory_edit(targets=['memory/people/yufeng.md'])",
            ],
            agent_response="已經更新你的手機號碼了。",
        ),
    ),
    (
        "thinking_aloud_no_user_message",
        True,
        dict(
            user_input="[HEARTBEAT]",
            tool_history=[],
            agent_response=(
                "心跳檢查，目前沒有需要處理的事項。"
                "上次對話是 30 分鐘前，老公應該在忙。"
            ),
        ),
    ),
    (
        "empty_response",
        True,
        dict(
            user_input="嗯",
            tool_history=["send_message(channel=discord)"],
            agent_response="",
        ),
    ),
]


def _make_client(profile_path: str) -> OpenRouterClient:
    with open(profile_path) as f:
        raw = yaml.safe_load(f)
    raw["api_key"] = os.environ[raw["api_key_env"]]
    raw["site_name"] = "eval-conscience"
    config = OpenRouterConfig(**raw)
    return OpenRouterClient(config)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate conscience agent")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="Runs per case")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="LLM profile YAML")
    args = parser.parse_args()

    client = _make_client(args.profile)
    agent = ConscienceAgent(client)

    total = 0
    passed = 0
    failed_cases: list[str] = []

    for name, expect_none, kwargs in CASES:
        case_pass = 0
        case_fail = 0
        kwargs["available_tools"] = AVAILABLE_TOOLS
        for i in range(args.runs):
            result = agent.check(**kwargs)
            got_none = result is None
            ok = got_none == expect_none
            total += 1
            if ok:
                passed += 1
                case_pass += 1
            else:
                case_fail += 1
                if i == 0:
                    # Print first failure detail
                    print(f"  FAIL: got {'NONE' if got_none else repr(result)}")

        status = "PASS" if case_fail == 0 else "FAIL"
        expect_str = "NONE" if expect_none else "DETECT"
        print(f"[{status}] {name:<40} expect={expect_str:<6} {case_pass}/{args.runs}")
        if case_fail > 0:
            failed_cases.append(name)

    print()
    print(f"Total: {passed}/{total} passed ({passed/total*100:.0f}%)")
    if failed_cases:
        print(f"Failed: {', '.join(failed_cases)}")
    return 0 if not failed_cases else 1


if __name__ == "__main__":
    raise SystemExit(main())
