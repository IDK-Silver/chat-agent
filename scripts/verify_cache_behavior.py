"""Verify prompt cache behavior using our actual code paths.

Uses ContextBuilder + Conversation + production brain client config
to build and send real API requests, then checks cache metrics.

Model: from cfgs/agent.yaml (agents.brain.llm).
Cost: ~$0.50 total for all test scenarios.

Usage:
    uv run python scripts/verify_cache_behavior.py
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from chat_agent.agent.core import setup_tools
from chat_agent.core.config import load_config
from chat_agent.context.builder import ContextBuilder
from chat_agent.context.conversation import Conversation
from chat_agent.llm.factory import create_client
from chat_agent.llm.schema import Message, ToolCall, ToolDefinition, ToolParameter
from chat_agent.memory.bm25_search import BM25MemorySearch
from chat_agent.workspace.manager import WorkspaceManager

APP_CONFIG = load_config("agent.yaml")
if "brain" not in APP_CONFIG.agents:
    print("Error: missing agents.brain in cfgs/agent.yaml")
    sys.exit(1)
_BRAIN_CFG = APP_CONFIG.agents["brain"]

if _BRAIN_CFG.llm.provider != "openrouter":
    print("Error: verify_cache_behavior.py currently requires brain provider=openrouter")
    sys.exit(1)

if not getattr(_BRAIN_CFG.llm, "api_key", None):
    print("Error: OpenRouter API key is missing for brain LLM config")
    sys.exit(1)

MODEL = _BRAIN_CFG.llm.model
_CACHE_TTL = _BRAIN_CFG.cache.ttl if _BRAIN_CFG.cache.enabled else "ephemeral"
_TIMEZONE = APP_CONFIG.timezone

# Filler for minimum cacheable length (2048 tokens for Sonnet)
FILLER = "Reference document. " + " ".join(
    f"Item {i}: species {i} found at depth {i * 10}m in sector {i % 20}, "
    f"population {i * 100}, first documented {1900 + i}."
    for i in range(1, 201)
)

# Simple tool for testing tool_calls flow
TEST_TOOL = ToolDefinition(
    name="get_current_time",
    description="Returns the current time. Use this when asked about the time.",
    parameters={
        "timezone": ToolParameter(type="string", description="Timezone name"),
    },
)


def _make_client():
    """Create brain client from cfgs/agent.yaml (same path as chat-cli)."""
    return create_client(
        _BRAIN_CFG.llm,
        transient_retries=_BRAIN_CFG.llm_transient_retries,
        request_timeout=_BRAIN_CFG.llm_request_timeout,
        rate_limit_retries=_BRAIN_CFG.llm_rate_limit_retries,
        retry_label="verify_cache_behavior",
    )


def _build_production_builder(agent_os_dir: Path) -> ContextBuilder:
    """Build ContextBuilder with the same config path used by chat-cli."""
    workspace = WorkspaceManager(agent_os_dir)
    system_prompt = workspace.get_system_prompt("brain")
    system_prompt = system_prompt.replace("{agent_os_dir}", str(agent_os_dir))
    builder = ContextBuilder(
        system_prompt=system_prompt,
        timezone=_TIMEZONE,
        agent_os_dir=agent_os_dir,
        boot_files=APP_CONFIG.context.boot_files,
        boot_files_as_tool=APP_CONFIG.context.boot_files_as_tool,
        max_chars=APP_CONFIG.context.max_chars,
        preserve_turns=APP_CONFIG.context.preserve_turns,
        provider=_BRAIN_CFG.llm.provider,
        cache_ttl=_CACHE_TTL,
    )
    builder.reload_boot_files()
    return builder


def _build_production_tools(agent_os_dir: Path) -> list[ToolDefinition]:
    """Build tool definitions via the same registry setup path as chat-cli."""
    bm25 = BM25MemorySearch(
        memory_dir=agent_os_dir / "memory",
        config=APP_CONFIG.tools.memory_search.bm25,
    )
    registry, _allowed_paths = setup_tools(
        APP_CONFIG.tools,
        agent_os_dir,
        bm25_search=bm25,
        brain_has_vision=_BRAIN_CFG.llm.get_vision(),
        use_own_vision_ability=_BRAIN_CFG.use_own_vision_ability,
    )
    return registry.get_definitions()


def _print_result(label: str, response) -> None:
    read = response.cache_read_tokens
    write = response.cache_write_tokens
    total = read + write
    hit_pct = (read / total * 100) if total > 0 else 0
    finish = response.finish_reason
    has_reasoning = response.reasoning_content is not None
    has_tools = len(response.tool_calls) > 0
    print(f"  cache_read={read:>6}, cache_write={write:>6}, "
          f"hit={hit_pct:>5.1f}%, finish={finish}, "
          f"reasoning={has_reasoning}, tool_calls={has_tools}")
    return read, write


def test_1_segmented_boot_cache():
    """Verify per-file boot segments cache independently."""
    print("\n=== Test 1: Segmented Boot Cache ===")
    print("Expect: changing one boot file doesn't invalidate others\n")

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        memory_dir = tmp_path / "memory" / "agent"
        memory_dir.mkdir(parents=True)
        (memory_dir / "context.md").write_text(FILLER, encoding="utf-8")
        (memory_dir / "notes.md").write_text(
            "Short notes file with minimal content.", encoding="utf-8",
        )

        builder = ContextBuilder(
            system_prompt="You are a helpful test assistant. Answer briefly. " + FILLER,
            timezone=_TIMEZONE,
            agent_os_dir=tmp_path,
            boot_files_as_tool=["memory/agent/context.md", "memory/agent/notes.md"],
            cache_ttl=_CACHE_TTL,
        )
        client = _make_client()

        # Call 1: establish cache
        builder.reload_boot_files()
        conv = Conversation()
        conv.add("user", "What is item 50?")
        messages = builder.build(conv)

        print("Call 1 (establish cache):")
        r1 = client.chat_with_tools(messages, [TEST_TOOL])
        read1, write1 = _print_result("call_1", r1)

        time.sleep(3)

        # Call 2: same content, expect cache hit
        conv2 = Conversation()
        conv2.add("user", "What is item 50?")
        messages2 = builder.build(conv2)

        print("Call 2 (same content, expect hit):")
        r2 = client.chat_with_tools(messages2, [TEST_TOOL])
        read2, write2 = _print_result("call_2", r2)

        time.sleep(3)

        # Call 3: change notes.md, keep context.md
        (memory_dir / "notes.md").write_text(
            "Updated notes with different content for cache test.", encoding="utf-8",
        )
        builder.reload_boot_files()
        conv3 = Conversation()
        conv3.add("user", "What is item 50?")
        messages3 = builder.build(conv3)

        print("Call 3 (notes.md changed, context.md unchanged, expect partial hit):")
        r3 = client.chat_with_tools(messages3, [TEST_TOOL])
        read3, write3 = _print_result("call_3", r3)

        # Verify: strict checks — cache_write must be 0 when content is identical
        ok = True
        if write2 != 0:
            print(f"\n  FAIL: Call 2 should have cache_write=0 (identical content), got {write2}")
            ok = False
        if read2 == 0:
            print("\n  FAIL: Call 2 should have cache_read > 0")
            ok = False
        if read3 == 0:
            print("\n  FAIL: Call 3 should have partial cache hit (context.md unchanged)")
            ok = False
        if ok:
            print("\n  PASS")
        return ok


def test_2_timestamp_stability():
    """Verify removing 'now' marker keeps prefix stable across turns."""
    print("\n=== Test 2: Timestamp Stability Across Turns ===")
    print("Expect: prefix before BP3 is identical across turns\n")

    from datetime import datetime, timezone

    builder = ContextBuilder(
        system_prompt="You are a test assistant. " + FILLER,
        timezone=_TIMEZONE,
        cache_ttl=_CACHE_TTL,
    )
    client = _make_client()

    ts1 = datetime(2026, 3, 3, 10, 0, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 3, 3, 10, 5, 0, tzinfo=timezone.utc)
    ts3 = datetime(2026, 3, 3, 10, 10, 0, tzinfo=timezone.utc)

    # Turn 1
    conv1 = Conversation()
    conv1.add("user", "Hello, how are you?", timestamp=ts1)
    conv1.add("assistant", "I am fine, thank you!", timestamp=ts1)
    conv1.add("user", "What day is it?", timestamp=ts2)
    messages1 = builder.build(conv1)

    print("Call 1 (turn 1):")
    r1 = client.chat_with_tools(messages1, [TEST_TOOL])
    read1, _ = _print_result("call_1", r1)

    time.sleep(3)

    # Turn 2: same prefix + new turn (simulates next turn)
    conv2 = Conversation()
    conv2.add("user", "Hello, how are you?", timestamp=ts1)
    conv2.add("assistant", "I am fine, thank you!", timestamp=ts1)
    conv2.add("user", "What day is it?", timestamp=ts2)
    conv2.add("assistant", "It is Tuesday.", timestamp=ts2)
    conv2.add("user", "Thanks!", timestamp=ts3)
    messages2 = builder.build(conv2)

    print("Call 2 (turn 2, prefix extended):")
    r2 = client.chat_with_tools(messages2, [TEST_TOOL])
    read2, _ = _print_result("call_2", r2)

    ok = True
    if read2 == 0:
        print("\n  FAIL: Call 2 should cache-hit on turn 1 prefix")
        ok = False
    if read2 <= read1:
        print(f"\n  FAIL: Call 2 cache_read ({read2}) should be > Call 1 ({read1}), "
              "indicating prefix grew and old prefix was cached")
        ok = False
    if ok:
        print(f"\n  PASS (call 2 reused {read2} cached tokens from turn 1)")
    return ok


def test_3_reasoning_roundtrip():
    """Verify reasoning content round-trip preserves cache."""
    print("\n=== Test 3: Reasoning Content Round-Trip ===")
    print("Expect: replaying assistant(reasoning+tool_calls) preserves cache\n")

    builder = ContextBuilder(
        system_prompt=(
            "You are a test assistant. When the user asks for the time, "
            "you MUST call the get_current_time tool. Think step by step. "
            + FILLER
        ),
        timezone=_TIMEZONE,
        cache_ttl=_CACHE_TTL,
    )
    client = _make_client()

    # Step 1: get a response with thinking + tool_calls
    conv = Conversation()
    conv.add("user", "What time is it right now? Use the tool.")
    messages = builder.build(conv)

    print("Call 1 (initial, expect thinking + tool_calls):")
    r1 = client.chat_with_tools(messages, [TEST_TOOL])
    read1, write1 = _print_result("call_1", r1)

    if not r1.has_tool_calls():
        print("\n  SKIP: LLM did not return tool_calls, cannot test round-trip")
        return True  # Not a failure, just can't test

    print(f"  LLM returned {len(r1.tool_calls)} tool_call(s), "
          f"reasoning={'yes' if r1.reasoning_content else 'no'}")

    time.sleep(3)

    # Step 2: add the response to conversation (with reasoning_details), add tool result
    conv.add_assistant_with_tools(
        r1.content,
        r1.tool_calls,
        reasoning_content=r1.reasoning_content,
        reasoning_details=r1.reasoning_details,
    )
    conv.add_tool_result(r1.tool_calls[0].id, "get_current_time", "2026-03-04 10:30 UTC+8")

    messages2 = builder.build(conv)

    print("Call 2 (continuation with reasoning round-trip):")
    r2 = client.chat_with_tools(messages2, [TEST_TOOL])
    read2, write2 = _print_result("call_2", r2)

    ok = True
    if read2 == 0:
        print("\n  FAIL: Call 2 has 0 cache read - reasoning round-trip broken!")
        ok = False
    if write2 > 0:
        print(f"\n  FAIL: Call 2 has cache_write={write2} - prefix changed, "
              "reasoning_details not properly round-tripped")
        ok = False
    if ok:
        print(f"\n  PASS (call 2 cache_read={read2}, cache_write={write2}, prefix preserved)")
    return ok


def test_4_chat_cli_parity_smoke():
    """Verify cache reuse using chat-cli production builder/tool setup."""
    print("\n=== Test 4: Chat-CLI Parity Smoke ===")
    print("Expect: second identical request reuses production prompt prefix\n")

    agent_os_dir = APP_CONFIG.get_agent_os_dir()
    try:
        builder = _build_production_builder(agent_os_dir)
    except FileNotFoundError as e:
        print(f"  SKIP: cannot load production workspace prompt/files: {e}")
        return True

    tools = _build_production_tools(agent_os_dir)
    client = _make_client()
    fixed_ts = datetime(2026, 3, 4, 10, 0, 0, tzinfo=timezone.utc)
    prompt = (
        "Parity smoke test. Reply with exactly: ok. "
        "Do not call any tool unless strictly necessary."
    )

    conv1 = Conversation()
    conv1.add("user", prompt, timestamp=fixed_ts)
    messages1 = builder.build(conv1)

    print(f"Call 1 (production setup, tools={len(tools)}):")
    r1 = client.chat_with_tools(messages1, tools)
    read1, write1 = _print_result("call_1", r1)

    time.sleep(3)

    conv2 = Conversation()
    conv2.add("user", prompt, timestamp=fixed_ts)
    messages2 = builder.build(conv2)

    print("Call 2 (same production request):")
    r2 = client.chat_with_tools(messages2, tools)
    read2, write2 = _print_result("call_2", r2)

    baseline = max(read1, write1)
    ok = True
    if baseline == 0:
        print("\n  FAIL: Call 1 had neither cache_read nor cache_write; cannot verify cache behavior")
        ok = False
    if read2 == 0:
        print("\n  FAIL: Call 2 should have cache_read > 0 in production parity test")
        ok = False
    if baseline > 0 and read2 < int(baseline * 0.8):
        print(f"\n  FAIL: Call 2 cache_read too low ({read2}) vs baseline ({baseline})")
        ok = False
    if baseline > 0 and write2 > max(64, int(baseline * 0.1)):
        print(f"\n  FAIL: Call 2 cache_write too high ({write2}) for identical request")
        ok = False
    if ok:
        print("\n  PASS")
    return ok


def main():
    print("=" * 60)
    print("Cache Behavior Verification (using actual code paths)")
    print(f"Model: {MODEL}")
    print("=" * 60)

    results = {}
    results["segmented_boot"] = test_1_segmented_boot_cache()
    results["timestamp_stability"] = test_2_timestamp_stability()
    results["reasoning_roundtrip"] = test_3_reasoning_roundtrip()
    results["chat_cli_parity"] = test_4_chat_cli_parity_smoke()

    print("\n" + "=" * 60)
    print("Summary:")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")

    all_pass = all(results.values())
    print("=" * 60)
    if not all_pass:
        sys.exit(1)


if __name__ == "__main__":
    main()
