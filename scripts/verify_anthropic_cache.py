#!/usr/bin/env python3
"""Verify prompt-cache behavior on the Anthropic native API.

Uses Claude Haiku 4.5 (cheapest Anthropic model) to verify that
cache_control breakpoints (BP1/BP2/BP3) work correctly across turns.

Scenarios:
1. Same-turn rebuild: identical request twice should cache-hit on call 2.
2. Multi-turn growth: adding a new turn should keep BP1/BP2 cached,
   and BP3 should shift forward so cache_read grows with conversation.
3. Tool-loop simulation: within a single turn, repeated calls with
   the same prefix should hit cache strongly.

Usage:
    uv run python scripts/verify_anthropic_cache.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from chat_agent.agent.responder import _advance_responder_cache_breakpoint
from chat_agent.context.builder import ContextBuilder
from chat_agent.context.conversation import Conversation
from chat_agent.core.config import resolve_llm_config
from chat_agent.core.schema import AnthropicConfig
from chat_agent.llm.factory import create_client
from chat_agent.llm.schema import LLMResponse, Message
from chat_agent.timezone_utils import configure as configure_timezone

PROFILE = "llm/anthropic/claude-haiku-4.5/no-thinking.yaml"
MODEL_CFG = resolve_llm_config(PROFILE)
if not isinstance(MODEL_CFG, AnthropicConfig):
    raise SystemExit(f"Expected AnthropicConfig, got {type(MODEL_CFG).__name__}")
if not MODEL_CFG.api_key:
    raise SystemExit("ANTHROPIC_API_KEY is missing.")

CACHE_TTL = "1h"

# Filler to push prompt above Anthropic's 1024-token cache minimum.
SYSTEM_FILLER = "Reference dossier. " + " ".join(
    f"Record {i}: habitat sector {i % 17}, observed depth {i * 3}m, "
    f"population estimate {i * 41}, first catalogued {1900 + i}."
    for i in range(1, 320)
)

BOOT_FILLER = "Core rules appendix. " + " ".join(
    f"Appendix {i}: policy {i} remains in force unless superseded by event {i + 7}."
    for i in range(1, 260)
)

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


def _make_client():
    return create_client(
        MODEL_CFG,
        transient_retries=0,
        request_timeout=MODEL_CFG.request_timeout,
        rate_limit_retries=0,
        retry_label="verify_anthropic_cache",
    )


def _write_boot_files(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory" / "agent"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "context.md").write_text(BOOT_FILLER, encoding="utf-8")


def _make_builder(tmp_path: Path) -> ContextBuilder:
    builder = ContextBuilder(
        system_prompt="You are a concise test assistant. " + SYSTEM_FILLER,
        timezone="Asia/Taipei",
        agent_os_dir=tmp_path,
        boot_files=["memory/agent/context.md"],
        preserve_turns=8,
        provider="anthropic",
        cache_ttl=CACHE_TTL,
    )
    builder.reload_boot_files()
    return builder


def _send(label: str, client, messages: list[Message]) -> LLMResponse:
    response = client.chat_with_tools(messages, [])
    read = response.cache_read_tokens or 0
    write = response.cache_write_tokens or 0
    prompt = response.prompt_tokens or 0
    ratio = (read / prompt * 100) if prompt else 0.0
    print(
        f"  {label:>30}: prompt={prompt:>6} "
        f"cache_read={read:>6} cache_write={write:>6} "
        f"hit={ratio:>5.1f}%"
    )
    return response


def _verify_payload_bp3(client, messages: list[Message]) -> bool:
    """Verify BP3 cache_control survives the full Pydantic serialization pipeline.

    This catches regressions where AnthropicMessagePayload coerces dicts
    into Pydantic models that drop cache_control.
    """
    system, chat_msgs = client._convert_messages(messages)
    serialized = client._serialize_messages(chat_msgs)

    # Check system blocks
    sys_bp = 0
    if isinstance(system, list):
        sys_bp = sum(1 for b in system if "cache_control" in b)

    # Check chat messages for BP3
    chat_bp = 0
    for m in serialized:
        if not isinstance(m.get("content"), list):
            continue
        for block in m["content"]:
            cc = block.get("cache_control")
            if cc is not None and cc:
                chat_bp += 1

    print(f"  Payload check: system BPs={sys_bp}, chat BPs={chat_bp}")
    if chat_bp == 0:
        print(f"  {FAIL}: BP3 cache_control lost in Pydantic serialization!")
        return False
    return True


def _describe_bp(messages: list[Message], label: str = "BP") -> None:
    """Print cache breakpoint locations for debugging."""
    for idx, msg in enumerate(messages):
        # Check Message-level cache_control (new path)
        if msg.cache_control is not None:
            text = (msg.content if isinstance(msg.content, str) else "")[:60].replace("\n", " ")
            print(f"  {label} @ idx={idx} role={msg.role} text={text!r}")
            continue
        # Legacy: ContentPart-level cache_control
        if isinstance(msg.content, list):
            for part in msg.content:
                if part.cache_control is not None:
                    text = (part.text or "")[:60].replace("\n", " ")
                    print(f"  {label} @ idx={idx} role={msg.role} text={text!r}")


def _sleep(seconds: float = 2.0) -> None:
    time.sleep(seconds)


# -- Scenario 1: Same-turn rebuild ------------------------------------------

def test_same_turn_rebuild(tmp_path: Path, client) -> bool:
    print("\n=== Scenario 1: Same-turn rebuild (should cache-hit on call 2) ===")
    builder = _make_builder(tmp_path)
    conv = Conversation()
    conv.add(
        "user", "What is 2+2?",
        timestamp=datetime(2026, 4, 4, 10, 0, tzinfo=timezone.utc),
        metadata={"turn_processing_started_at": "2026-04-04T18:00:00+08:00"},
    )
    messages = _advance_responder_cache_breakpoint(builder.build(conv))
    _describe_bp(messages)

    r1 = _send("call 1 (cold)", client, messages)
    _sleep()
    r2 = _send("call 2 (warm)", client, messages)

    read2 = r2.cache_read_tokens or 0
    prompt2 = r2.prompt_tokens or 0
    hit_rate = (read2 / prompt2 * 100) if prompt2 else 0
    ok = hit_rate > 80
    print(f"  Result: call 2 cache hit = {hit_rate:.1f}% {'(' + PASS + ')' if ok else '(' + FAIL + ')'}")
    return ok


# -- Scenario 2: Multi-turn growth ------------------------------------------

def test_multi_turn_growth(tmp_path: Path, client) -> bool:
    print("\n=== Scenario 2: Multi-turn growth (cache_read should grow) ===")
    builder = _make_builder(tmp_path)

    conv = Conversation()
    conv.add(
        "user", "Turn 1: What is the capital of France?",
        timestamp=datetime(2026, 4, 4, 10, 0, tzinfo=timezone.utc),
        metadata={"turn_processing_started_at": "2026-04-04T18:00:00+08:00"},
    )

    # Turn 1 - cold
    msgs1 = _advance_responder_cache_breakpoint(builder.build(conv))
    r1 = _send("turn 1 call 1 (cold)", client, msgs1)
    _sleep()
    r1b = _send("turn 1 call 2 (warm)", client, msgs1)
    _sleep()

    # Add assistant reply and new user turn
    conv.add("assistant", "Paris is the capital of France.",
             timestamp=datetime(2026, 4, 4, 10, 1, tzinfo=timezone.utc))
    conv.add(
        "user", "Turn 2: What about Germany?",
        timestamp=datetime(2026, 4, 4, 10, 2, tzinfo=timezone.utc),
        metadata={"turn_processing_started_at": "2026-04-04T18:02:00+08:00"},
    )

    msgs2 = _advance_responder_cache_breakpoint(builder.build(conv))
    _describe_bp(msgs2)
    r2 = _send("turn 2 call 1", client, msgs2)
    _sleep()
    r2b = _send("turn 2 call 2", client, msgs2)

    read_1b = r1b.cache_read_tokens or 0
    read_2b = r2b.cache_read_tokens or 0
    # Turn 2 should cache at least as much as turn 1 (system prefix is stable)
    ok = read_2b >= read_1b * 0.8 and read_2b > 0
    print(f"  Result: turn1 read={read_1b}, turn2 read={read_2b} {'(' + PASS + ')' if ok else '(' + FAIL + ')'}")
    return ok


# -- Scenario 3: Tool-loop simulation ---------------------------------------

def test_tool_loop(tmp_path: Path, client) -> bool:
    print("\n=== Scenario 3: Tool-loop (3 calls same turn, cache should compound) ===")
    builder = _make_builder(tmp_path)

    conv = Conversation()
    conv.add(
        "user", "Help me plan a trip to Japan.",
        timestamp=datetime(2026, 4, 4, 10, 0, tzinfo=timezone.utc),
        metadata={"turn_processing_started_at": "2026-04-04T18:00:00+08:00"},
    )

    messages = _advance_responder_cache_breakpoint(builder.build(conv))
    reads = []
    for i in range(3):
        r = _send(f"tool-loop call {i+1}", client, messages)
        reads.append(r.cache_read_tokens or 0)
        _sleep(1.5)

    # Call 2 and 3 should have strong cache hits
    ok = reads[1] > 0 and reads[2] > 0 and reads[2] >= reads[1] * 0.9
    print(f"  Result: reads={reads} {'(' + PASS + ')' if ok else '(' + FAIL + ')'}")
    return ok


# -- Scenario 4: Cross-turn prefix stability (render cache) -----------------

def test_cross_turn_stability(tmp_path: Path, client) -> bool:
    """Verify that cross-turn first-call cache hit is near 100%.

    Without render cache, the first call of each new turn drops to ~55%
    because dynamic injections ([Runtime Context]) disappear from old
    messages when they are no longer the latest user message.
    With render cache, old messages retain their injected content.
    """
    print("\n=== Scenario 4: Cross-turn prefix stability (render cache) ===")
    builder = _make_builder(tmp_path)

    conv = Conversation()
    conv.add(
        "user", "Turn 1: What is the capital of France?",
        timestamp=datetime(2026, 4, 4, 10, 0, tzinfo=timezone.utc),
        metadata={"turn_processing_started_at": "2026-04-04T18:00:00+08:00"},
    )

    # Turn 1: warm the cache
    msgs1 = _advance_responder_cache_breakpoint(builder.build(conv))
    r1 = _send("turn 1 (cold)", client, msgs1)
    _sleep()
    r1b = _send("turn 1 (warm)", client, msgs1)
    _sleep()

    # Turn 2
    conv.add("assistant", "Paris is the capital of France.",
             timestamp=datetime(2026, 4, 4, 10, 1, tzinfo=timezone.utc))
    conv.add(
        "user", "Turn 2: What about Germany?",
        timestamp=datetime(2026, 4, 4, 10, 2, tzinfo=timezone.utc),
        metadata={"turn_processing_started_at": "2026-04-04T18:02:00+08:00"},
    )

    msgs2 = _advance_responder_cache_breakpoint(builder.build(conv))
    r2 = _send("turn 2 first call", client, msgs2)
    _sleep()

    # Turn 3
    conv.add("assistant", "Berlin is the capital of Germany.",
             timestamp=datetime(2026, 4, 4, 10, 3, tzinfo=timezone.utc))
    conv.add(
        "user", "Turn 3: And Japan?",
        timestamp=datetime(2026, 4, 4, 10, 4, tzinfo=timezone.utc),
        metadata={"turn_processing_started_at": "2026-04-04T18:04:00+08:00"},
    )

    msgs3 = _advance_responder_cache_breakpoint(builder.build(conv))
    r3 = _send("turn 3 first call", client, msgs3)

    # Key metric: cross-turn first-call cache rate.
    # Without render cache: ~55% (only BP1+BP2).
    # With render cache: should be >90% (conversation prefix stable).
    prompt3 = r3.prompt_tokens or 1
    read3 = r3.cache_read_tokens or 0
    rate3 = read3 / prompt3 * 100

    prompt2 = r2.prompt_tokens or 1
    read2 = r2.cache_read_tokens or 0
    rate2 = read2 / prompt2 * 100

    ok = rate2 > 85 and rate3 > 85
    print(f"  Result: turn2 first-call={rate2:.1f}%, turn3 first-call={rate3:.1f}% "
          f"{'(' + PASS + ')' if ok else '(' + FAIL + ')'}")
    if not ok:
        print(f"  (Expected >85%. If ~55%, render cache is not preventing prefix divergence)")
    return ok


# -- Scenario 5: Render cache persistence across simulated restart ----------

def test_render_cache_persistence(tmp_path: Path, client) -> bool:
    """Simulate restart: build 2 turns, export render cache, create a fresh
    builder, import the cache, then verify cross-turn first-call hits ~99%.
    Without import, rate would drop to ~55% (BP1+BP2 only).
    """
    print("\n=== Scenario 5: Render cache persistence (simulated restart) ===")

    # Phase 1: Build 2 turns with original builder (populates render cache)
    builder1 = _make_builder(tmp_path)
    conv = Conversation()
    conv.add(
        "user", "Turn 1: What is the capital of France?",
        timestamp=datetime(2026, 4, 4, 10, 0, tzinfo=timezone.utc),
        metadata={"turn_processing_started_at": "2026-04-04T18:00:00+08:00"},
    )
    msgs1 = _advance_responder_cache_breakpoint(builder1.build(conv))
    _send("turn 1 (cold)", client, msgs1)
    _sleep()
    _send("turn 1 (warm)", client, msgs1)
    _sleep()

    conv.add("assistant", "Paris is the capital of France.",
             timestamp=datetime(2026, 4, 4, 10, 1, tzinfo=timezone.utc))
    conv.add(
        "user", "Turn 2: What about Germany?",
        timestamp=datetime(2026, 4, 4, 10, 2, tzinfo=timezone.utc),
        metadata={"turn_processing_started_at": "2026-04-04T18:02:00+08:00"},
    )
    msgs2 = _advance_responder_cache_breakpoint(builder1.build(conv))
    _send("turn 2 (warm)", client, msgs2)
    _sleep()

    # Phase 2: Export render cache (simulates write to disk)
    exported = builder1.export_render_cache()
    fingerprint = builder1.boot_fingerprint()
    print(f"  Exported {len(exported)} cached entries, fp={fingerprint[:8]}...")

    # Phase 3: Create fresh builder (simulates restart)
    builder2 = _make_builder(tmp_path)
    # Without import, this would clear render cache in reload_boot_files()

    # Phase 4: Import render cache into fresh builder
    all_msgs = conv.get_messages()
    builder2.import_render_cache(exported, list(all_msgs[:len(exported)]))
    print(f"  Imported {len(exported)} cached entries into fresh builder")

    # Phase 5: Add turn 3 and verify cache hit
    conv.add("assistant", "Berlin is the capital of Germany.",
             timestamp=datetime(2026, 4, 4, 10, 3, tzinfo=timezone.utc))
    conv.add(
        "user", "Turn 3: And Japan?",
        timestamp=datetime(2026, 4, 4, 10, 4, tzinfo=timezone.utc),
        metadata={"turn_processing_started_at": "2026-04-04T18:04:00+08:00"},
    )
    msgs3 = _advance_responder_cache_breakpoint(builder2.build(conv))
    r3 = _send("turn 3 first call (after restart)", client, msgs3)

    prompt3 = r3.prompt_tokens or 1
    read3 = r3.cache_read_tokens or 0
    rate3 = read3 / prompt3 * 100

    ok = rate3 > 85
    print(
        f"  Result: turn3 first-call={rate3:.1f}% "
        f"{'(' + PASS + ')' if ok else '(' + FAIL + ')'}"
    )
    if not ok:
        print(f"  (Expected >85%. If ~55%, render cache import is not working)")
    return ok


# -- Main --------------------------------------------------------------------

def main() -> None:
    configure_timezone("Asia/Taipei")
    print("=" * 72)
    print("Anthropic Native API - Prompt Cache Verification")
    print(f"Model: {MODEL_CFG.model}")
    print(f"Profile: {PROFILE}")
    print(f"Cache TTL: {CACHE_TTL}")
    print("=" * 72)

    client = _make_client()
    results: dict[str, bool] = {}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _write_boot_files(tmp_path)

        results["same_turn_rebuild"] = test_same_turn_rebuild(tmp_path, client)
        _sleep(3)
        results["multi_turn_growth"] = test_multi_turn_growth(tmp_path, client)
        _sleep(3)
        results["tool_loop"] = test_tool_loop(tmp_path, client)
        _sleep(3)
        results["cross_turn_stability"] = test_cross_turn_stability(tmp_path, client)
        _sleep(3)
        results["render_cache_persistence"] = test_render_cache_persistence(tmp_path, client)

    print("\n" + "=" * 72)
    print("Summary")
    print("=" * 72)
    all_pass = True
    for name, ok in results.items():
        status = PASS if ok else FAIL
        print(f"  {name:>25}: {status}")
        if not ok:
            all_pass = False

    if all_pass:
        print(f"\nAll scenarios {PASS}. Cache breakpoints working on Anthropic native API.")
    else:
        print(f"\nSome scenarios {FAIL}. Check breakpoint injection.")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
