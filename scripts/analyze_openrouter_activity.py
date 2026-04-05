#!/usr/bin/env python3
"""Analyze OpenRouter activity CSV and session traces for cost tuning."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ActivityRow:
    generation_id: str
    created_at: str
    model_permaslug: str
    tokens_prompt: float
    tokens_cached: float
    tokens_completion: float
    byok_usage_inference: float
    cost_cache: float

    @property
    def uncached_tokens(self) -> float:
        return max(0.0, self.tokens_prompt - self.tokens_cached)

    @property
    def no_cache_equivalent_cost(self) -> float:
        # OpenRouter CSV convention:
        # effective_no_cache = byok_usage_inference - cost_cache
        return self.byok_usage_inference - self.cost_cache


@dataclass
class SessionMetrics:
    sessions: int = 0
    user_turns: int = 0
    llm_rounds_total: int = 0
    llm_round_dist: Counter[int] = None  # type: ignore[assignment]
    send_turns: int = 0
    send_calls: int = 0
    send_calls_dist: Counter[int] = None  # type: ignore[assignment]
    turns_with_tool_errors: int = 0
    send_chars_total: int = 0
    send_chars_extra_calls: int = 0
    end_of_turn_candidate_turns: int = 0
    end_of_turn_est_saved_calls: int = 0

    def __post_init__(self) -> None:
        if self.llm_round_dist is None:
            self.llm_round_dist = Counter()
        if self.send_calls_dist is None:
            self.send_calls_dist = Counter()


def _to_float(value: str) -> float:
    if value is None:
        return 0.0
    value = value.strip()
    if not value:
        return 0.0
    return float(value)


def _pct(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = max(0, min(len(sorted_values) - 1, math.ceil(q * len(sorted_values)) - 1))
    return sorted_values[idx]


def _load_rows(csv_path: Path, model: str | None) -> list[ActivityRow]:
    rows: list[ActivityRow] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            model_permaslug = (r.get("model_permaslug") or "").strip()
            if model and model_permaslug != model:
                continue
            rows.append(
                ActivityRow(
                    generation_id=(r.get("generation_id") or "").strip(),
                    created_at=(r.get("created_at") or "").strip(),
                    model_permaslug=model_permaslug,
                    tokens_prompt=_to_float(r.get("tokens_prompt") or ""),
                    tokens_cached=_to_float(r.get("tokens_cached") or ""),
                    tokens_completion=_to_float(r.get("tokens_completion") or ""),
                    byok_usage_inference=_to_float(r.get("byok_usage_inference") or ""),
                    cost_cache=_to_float(r.get("cost_cache") or ""),
                )
            )
    return rows


def _recommend_max_chars(
    *,
    current_max_chars: int,
    p99_prompt_tokens: float,
    token_target_p99: int,
) -> tuple[int, int, int]:
    """Return (conservative, balanced, ceiling) max_chars suggestions."""
    if p99_prompt_tokens <= 0:
        return current_max_chars, current_max_chars, current_max_chars

    ceiling = int(round(current_max_chars * (token_target_p99 / p99_prompt_tokens)))
    balanced = int(round(min(ceiling, current_max_chars * 1.15)))
    conservative = int(round(current_max_chars * 0.9))

    conservative = max(20_000, conservative)
    balanced = max(conservative, balanced)
    ceiling = max(balanced, ceiling)
    return conservative, balanced, ceiling


def _split_turns(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for entry in entries:
        role = ((entry.get("message") or {}).get("role"))
        if role == "user" and current:
            turns.append(current)
            current = []
        current.append(entry)
    if current:
        turns.append(current)
    return turns


def _send_call_payload_chars(turn: list[dict[str, Any]]) -> list[int]:
    """Return payload chars for each send_message tool call in this turn."""
    result_map: dict[str, str] = {}
    for entry in turn:
        msg = entry.get("message") or {}
        if msg.get("role") != "tool":
            continue
        if msg.get("name") != "send_message":
            continue
        tool_call_id = msg.get("tool_call_id")
        content = msg.get("content")
        if isinstance(tool_call_id, str) and isinstance(content, str):
            result_map[tool_call_id] = content

    payloads: list[int] = []
    for entry in turn:
        msg = entry.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            if tc.get("name") != "send_message":
                continue
            args = tc.get("arguments")
            if not isinstance(args, dict):
                continue
            tool_call_id = tc.get("id")
            result_text = result_map.get(tool_call_id, "") if isinstance(tool_call_id, str) else ""
            args_len = len(json.dumps(args, ensure_ascii=False, separators=(",", ":")))
            payloads.append(args_len + len(result_text))
    return payloads


def _parse_session_metrics(
    *,
    sessions_dir: Path,
    session_prefix: str | None,
    allowed_tools: set[str],
    allowed_schedule_actions: set[str],
) -> SessionMetrics:
    metrics = SessionMetrics()
    if not sessions_dir.is_dir():
        return metrics

    session_dirs = sorted(
        d for d in sessions_dir.iterdir()
        if d.is_dir() and (session_prefix is None or d.name.startswith(session_prefix))
    )
    metrics.sessions = len(session_dirs)

    for sdir in session_dirs:
        jsonl = sdir / "messages.jsonl"
        if not jsonl.is_file():
            continue
        entries: list[dict[str, Any]] = []
        for raw in jsonl.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                continue

        for turn in _split_turns(entries):
            has_user = any(((e.get("message") or {}).get("role") == "user") for e in turn)
            if not has_user:
                continue
            metrics.user_turns += 1

            llm_rounds = 0
            send_calls = 0
            has_tool_error = False
            result_by_call_id: dict[str, str] = {}
            rounds: list[list[dict[str, Any]]] = []

            for entry in turn:
                msg = entry.get("message") or {}
                role = msg.get("role")
                if role == "assistant":
                    tool_calls = msg.get("tool_calls")
                    if isinstance(tool_calls, list) and tool_calls:
                        llm_rounds += 1
                        rounds.append([tc for tc in tool_calls if isinstance(tc, dict)])
                        send_calls += sum(1 for tc in tool_calls if isinstance(tc, dict) and tc.get("name") == "send_message")
                elif role == "tool":
                    content = msg.get("content")
                    tool_call_id = msg.get("tool_call_id")
                    if isinstance(content, str) and content.startswith("Error:"):
                        has_tool_error = True
                    if isinstance(tool_call_id, str) and isinstance(content, str):
                        result_by_call_id[tool_call_id] = content

            metrics.llm_rounds_total += llm_rounds
            metrics.llm_round_dist[llm_rounds] += 1
            if has_tool_error:
                metrics.turns_with_tool_errors += 1
            if send_calls > 0:
                metrics.send_turns += 1
                metrics.send_calls += send_calls
                metrics.send_calls_dist[send_calls] += 1

            send_payloads = _send_call_payload_chars(turn)
            if send_payloads:
                metrics.send_chars_total += sum(send_payloads)
                metrics.send_chars_extra_calls += sum(send_payloads[1:])

            # terminal_tool_end_of_turn candidates + estimated saved calls
            short_at: int | None = None
            for idx, round_tool_calls in enumerate(rounds, start=1):
                if not round_tool_calls:
                    continue
                round_ok = True
                for tc in round_tool_calls:
                    tool_name = tc.get("name")
                    if not isinstance(tool_name, str) or tool_name not in allowed_tools:
                        round_ok = False
                        break
                    if tool_name == "schedule_action":
                        args = tc.get("arguments")
                        action = args.get("action") if isinstance(args, dict) else None
                        if not isinstance(action, str) or action not in allowed_schedule_actions:
                            round_ok = False
                            break
                    call_id = tc.get("id")
                    result = result_by_call_id.get(call_id) if isinstance(call_id, str) else None
                    if not isinstance(result, str) or result.startswith("Error:"):
                        round_ok = False
                        break
                if round_ok:
                    short_at = idx
                    break

            if short_at is not None:
                metrics.end_of_turn_candidate_turns += 1
                # Skip remaining tool rounds + final follow-up LLM call.
                metrics.end_of_turn_est_saved_calls += (len(rounds) - short_at) + 1

    return metrics


def _parse_csv_list(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        required=True,
        help="Path to OpenRouter activity CSV export.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Filter by exact model_permaslug (e.g. anthropic/claude-4.6-sonnet-20260217).",
    )
    parser.add_argument(
        "--current-max-chars",
        type=int,
        default=128_000,
        help="Current context.max_chars for recommendation scaling.",
    )
    parser.add_argument(
        "--long-threshold",
        type=int,
        default=200_000,
        help="Long-context premium threshold in prompt tokens.",
    )
    parser.add_argument(
        "--token-target-p99",
        type=int,
        default=180_000,
        help="Target p99 prompt-token ceiling for safer cost headroom.",
    )
    parser.add_argument(
        "--sessions-dir",
        default=None,
        help="Optional path to brain session dir (contains */messages.jsonl).",
    )
    parser.add_argument(
        "--session-prefix",
        default=None,
        help="Optional session dir prefix filter (e.g. 20260302_).",
    )
    parser.add_argument(
        "--end-of-turn-tools",
        default="send_message,schedule_action",
        help="Comma-separated allowed tools for end-of-turn candidate estimation.",
    )
    parser.add_argument(
        "--end-of-turn-schedule-actions",
        default="add,remove",
        help="Comma-separated allowed schedule_action actions.",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser().resolve()
    rows = _load_rows(csv_path, args.model)
    if not rows:
        print("No matching rows. Check --csv path and --model filter.")
        return 1

    prompts = sorted(r.tokens_prompt for r in rows)
    cached = sorted(r.tokens_cached for r in rows)
    actual_costs = sorted(r.byok_usage_inference for r in rows)
    no_cache_costs = sorted(r.no_cache_equivalent_cost for r in rows)

    total_prompt = sum(prompts)
    total_cached = sum(cached)
    total_uncached = max(0.0, total_prompt - total_cached)
    call_count = len(rows)
    hit_count = sum(1 for r in rows if r.tokens_cached > 0)
    hit_rate = hit_count / call_count if call_count else 0.0
    weighted_cache_ratio = (total_cached / total_prompt) if total_prompt else 0.0

    total_actual = sum(actual_costs)
    total_no_cache = sum(no_cache_costs)
    total_saved = total_no_cache - total_actual
    saved_ratio = (total_saved / total_no_cache) if total_no_cache > 0 else 0.0

    p50 = _pct(prompts, 0.50)
    p90 = _pct(prompts, 0.90)
    p95 = _pct(prompts, 0.95)
    p99 = _pct(prompts, 0.99)
    p100 = _pct(prompts, 1.00)

    long_count = sum(1 for p in prompts if p > args.long_threshold)
    near_count = sum(1 for p in prompts if p > args.long_threshold * 0.9)

    conservative, balanced, ceiling = _recommend_max_chars(
        current_max_chars=args.current_max_chars,
        p99_prompt_tokens=p99,
        token_target_p99=args.token_target_p99,
    )

    sample_model = rows[0].model_permaslug
    print("OpenRouter Activity Analysis")
    print(f"CSV: {csv_path}")
    print(f"Model: {sample_model}")
    print(f"Rows: {call_count}")
    print()
    print("Prompt Tokens")
    print(f"- p50: {int(round(p50)):,}")
    print(f"- p90: {int(round(p90)):,}")
    print(f"- p95: {int(round(p95)):,}")
    print(f"- p99: {int(round(p99)):,}")
    print(f"- max: {int(round(p100)):,}")
    print()
    print("Cache")
    print(f"- hit calls (tokens_cached > 0): {hit_count}/{call_count} ({hit_rate*100:.1f}%)")
    print(f"- weighted cache ratio (cached/prompt): {weighted_cache_ratio*100:.1f}%")
    print(f"- total prompt: {int(round(total_prompt)):,}")
    print(f"- total cached: {int(round(total_cached)):,}")
    print(f"- total uncached: {int(round(total_uncached)):,}")
    print()
    print("Cost (BYOK, from CSV)")
    print(f"- total actual (byok_usage_inference): ${total_actual:.4f}")
    print(f"- total no-cache equivalent: ${total_no_cache:.4f}")
    print(f"- total cache savings: ${total_saved:.4f} ({saved_ratio*100:.1f}%)")
    print(
        "- per-call actual p50/p90/max: "
        f"${_pct(actual_costs,0.5):.4f} / ${_pct(actual_costs,0.9):.4f} / ${_pct(actual_costs,1.0):.4f}"
    )
    print()
    print("Long-Context Threshold Risk")
    print(f"- threshold: {args.long_threshold:,} prompt tokens")
    print(f"- near-threshold (>90%): {near_count}/{call_count}")
    print(f"- over-threshold: {long_count}/{call_count}")
    if long_count == 0:
        print("- note: no observed samples above threshold; no empirical surcharge curve yet")
    print()
    print("Suggested context.max_chars")
    print(f"- current: {args.current_max_chars:,}")
    print(f"- conservative: {conservative:,}")
    print(f"- balanced: {balanced:,}")
    print(f"- token-safe ceiling (p99≈{args.token_target_p99:,}): {ceiling:,}")
    print("- method: scale by observed prompt-token p99; validate with fresh 3-7 day data")

    if args.sessions_dir:
        sessions_dir = Path(args.sessions_dir).expanduser().resolve()
        allowed_tools = _parse_csv_list(args.end_of_turn_tools)
        allowed_schedule_actions = _parse_csv_list(args.end_of_turn_schedule_actions)
        sm = _parse_session_metrics(
            sessions_dir=sessions_dir,
            session_prefix=args.session_prefix,
            allowed_tools=allowed_tools,
            allowed_schedule_actions=allowed_schedule_actions,
        )

        print()
        print("Session Metrics")
        print(f"- sessions scanned: {sm.sessions}")
        print(f"- user turns: {sm.user_turns}")
        if sm.user_turns:
            avg_llm_rounds = sm.llm_rounds_total / sm.user_turns
            print(f"- avg llm tool-rounds/turn: {avg_llm_rounds:.3f}")
        print(f"- llm tool-round distribution: {dict(sorted(sm.llm_round_dist.items()))}")
        print(f"- turns with tool errors: {sm.turns_with_tool_errors}")
        print(f"- send turns: {sm.send_turns}")
        if sm.send_turns:
            print(f"- avg send calls/send-turn: {sm.send_calls / sm.send_turns:.3f}")
        print(f"- send call distribution: {dict(sorted(sm.send_calls_dist.items()))}")
        extra_calls = max(0, sm.send_calls - sm.send_turns)
        print(f"- extra send calls over 1 per send-turn: {extra_calls}")

        print()
        print("Segmented Send Savings Estimate")
        print(f"- total send payload chars (args+tool result): {sm.send_chars_total:,}")
        print(f"- chars in extra send calls: {sm.send_chars_extra_calls:,}")
        est_70 = int(round(sm.send_chars_extra_calls * 0.7))
        print(f"- estimated saved chars (drop all extra): {sm.send_chars_extra_calls:,}")
        print(f"- estimated saved chars (70% conservative): {est_70:,}")
        print(f"- estimated saved tokens @4 chars/token: {sm.send_chars_extra_calls/4:.1f} (70%: {est_70/4:.1f})")
        print(f"- estimated saved tokens @3.2 chars/token: {sm.send_chars_extra_calls/3.2:.1f} (70%: {est_70/3.2:.1f})")

        print()
        print("terminal_tool_end_of_turn Estimate")
        print(f"- allowed tools: {sorted(allowed_tools)}")
        print(f"- schedule_action allowed actions: {sorted(allowed_schedule_actions)}")
        print(f"- candidate turns: {sm.end_of_turn_candidate_turns}")
        print(f"- estimated saved LLM calls: {sm.end_of_turn_est_saved_calls}")
        if sm.user_turns:
            print(f"- estimated saved calls per user turn: {sm.end_of_turn_est_saved_calls/sm.user_turns:.3f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
