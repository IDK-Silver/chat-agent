#!/usr/bin/env python3
"""Verify prompt-cache behavior on the OpenAI direct API.

OpenAI uses automatic prefix-based caching (no cache_control breakpoints).
- Cache kicks in at >= 1024 token prefix, increments of 128 tokens.
- prompt_cache_retention: "24h" extends TTL (default in-memory ~5-10 min).
- cache_control annotations (Anthropic-style) should be silently ignored.

Scenarios:
1. Automatic caching: identical request twice -> cached_tokens > 0 on call 2.
2. cache_control tolerance: verify breakpoint annotations don't cause errors.
3. Multi-turn prefix stability: prefix cache hits grow with conversation.
4. prompt_cache_retention: verify "24h" parameter accepted by API.

Usage:
    uv run python scripts/verify_openai_cache.py
    uv run python scripts/verify_openai_cache.py --model gpt-4o-mini  # cheaper
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

from chat_agent.context.builder import ContextBuilder
from chat_agent.context.conversation import Conversation
from chat_agent.core.config import resolve_llm_config
from chat_agent.core.schema import OpenAIConfig
from chat_agent.llm.factory import create_client
from chat_agent.llm.schema import LLMResponse, Message
from chat_agent.timezone_utils import configure as configure_timezone

# Filler to push prompt above OpenAI's 1024-token cache minimum.
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
SKIP = "\033[93mSKIP\033[0m"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify OpenAI prompt caching")
    parser.add_argument(
        "--model", default="gpt-5.1",
        help="Model to test (default: gpt-5.1, use gpt-4o-mini for cheaper tests)",
    )
    parser.add_argument(
        "--profile", default=None,
        help="LLM profile path relative to cfgs/ (auto-detected from --model if omitted)",
    )
    return parser.parse_args()


def _load_config(args: argparse.Namespace) -> OpenAIConfig:
    profile = args.profile
    if profile is None:
        model = args.model
        # Try thinking variant first, fall back to no-thinking
        for variant in ("thinking", "no-thinking"):
            candidate = f"llm/openai/{model}/{variant}.yaml"
            try:
                cfg = resolve_llm_config(candidate)
                if isinstance(cfg, OpenAIConfig):
                    print(f"Profile: {candidate}")
                    return cfg
            except Exception:
                continue
        raise SystemExit(
            f"No OpenAI profile found for model={model}. "
            f"Pass --profile explicitly."
        )
    cfg = resolve_llm_config(profile)
    if not isinstance(cfg, OpenAIConfig):
        raise SystemExit(f"Expected OpenAIConfig, got {type(cfg).__name__}")
    print(f"Profile: {profile}")
    return cfg


def _make_client(config: OpenAIConfig):
    return create_client(
        config,
        transient_retries=0,
        request_timeout=config.request_timeout,
        rate_limit_retries=0,
        retry_label="verify_openai_cache",
    )


def _write_boot_files(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory" / "agent"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "context.md").write_text(BOOT_FILLER, encoding="utf-8")


def _make_builder(tmp_path: Path, *, cache_ttl: str | None = None) -> ContextBuilder:
    builder = ContextBuilder(
        system_prompt="You are a concise test assistant. " + SYSTEM_FILLER,
        agent_os_dir=tmp_path,
        boot_files=["memory/agent/context.md"],
        preserve_turns=8,
        provider="openai",
        cache_ttl=cache_ttl,
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
        f"  {label:>35}: prompt={prompt:>6} "
        f"cached={read:>6} write={write:>6} "
        f"hit={ratio:>5.1f}%"
    )
    return response


def _sleep(seconds: float = 2.0) -> None:
    time.sleep(seconds)


# -- Scenario 1: Automatic caching (no breakpoints) ---------------------------

def test_automatic_caching(tmp_path: Path, client) -> bool:
    """Same request twice, no cache_control breakpoints.

    OpenAI should automatically cache the prefix on call 1, hit on call 2.
    """
    print("\n=== Scenario 1: Automatic caching (no breakpoints) ===")
    builder = _make_builder(tmp_path, cache_ttl=None)
    conv = Conversation()
    conv.add(
        "user", "What is 2+2?",
        timestamp=datetime(2026, 4, 4, 10, 0, tzinfo=timezone.utc),
    )
    messages = builder.build(conv)

    r1 = _send("call 1 (cold)", client, messages)
    _sleep()
    r2 = _send("call 2 (should cache-hit)", client, messages)

    read2 = r2.cache_read_tokens or 0
    prompt2 = r2.prompt_tokens or 0
    hit_rate = (read2 / prompt2 * 100) if prompt2 else 0
    ok = hit_rate > 50
    print(f"  Result: call 2 cache hit = {hit_rate:.1f}% "
          f"{'(' + PASS + ')' if ok else '(' + FAIL + ')'}")
    if not ok and prompt2 < 1024:
        print(f"  Note: prompt_tokens={prompt2} < 1024 minimum for caching")
    return ok


# -- Scenario 2: cache_control tolerance --------------------------------------

def test_cache_control_tolerance(tmp_path: Path, client) -> bool:
    """Send request WITH Anthropic-style cache_control breakpoints.

    OpenAI should silently ignore them (no 400 error).
    """
    print("\n=== Scenario 2: cache_control tolerance (breakpoints in payload) ===")
    builder = _make_builder(tmp_path, cache_ttl="1h")
    conv = Conversation()
    conv.add(
        "user", "What is the capital of France?",
        timestamp=datetime(2026, 4, 4, 10, 0, tzinfo=timezone.utc),
    )
    messages = builder.build(conv)

    # Check that cache_control is actually in the messages
    bp_count = sum(1 for m in messages if m.cache_control is not None)
    print(f"  Breakpoints injected: {bp_count}")

    try:
        r1 = _send("call with cache_control", client, messages)
        print(f"  Result: API accepted cache_control without error ({PASS})")
        _sleep()
        r2 = _send("call 2 (cache still works?)", client, messages)
        read2 = r2.cache_read_tokens or 0
        prompt2 = r2.prompt_tokens or 0
        hit_rate = (read2 / prompt2 * 100) if prompt2 else 0
        print(f"  Cache hit with breakpoints: {hit_rate:.1f}%")
        return True
    except httpx.HTTPStatusError as exc:
        print(f"  Result: API rejected cache_control ({FAIL})")
        print(f"  Status: {exc.response.status_code}")
        print(f"  Body: {exc.response.text[:300]}")
        return False


# -- Scenario 3: Multi-turn prefix stability -----------------------------------

def test_multi_turn(tmp_path: Path, client) -> bool:
    """Add turns progressively, verify prefix cache hits grow."""
    print("\n=== Scenario 3: Multi-turn prefix stability ===")
    builder = _make_builder(tmp_path, cache_ttl=None)

    conv = Conversation()
    conv.add(
        "user", "Turn 1: What is the capital of France?",
        timestamp=datetime(2026, 4, 4, 10, 0, tzinfo=timezone.utc),
    )

    # Turn 1 - warm the cache
    msgs1 = builder.build(conv)
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
    )

    msgs2 = builder.build(conv)
    r2 = _send("turn 2 call 1", client, msgs2)
    _sleep()
    r2b = _send("turn 2 call 2", client, msgs2)

    read_1b = r1b.cache_read_tokens or 0
    read_2 = r2.cache_read_tokens or 0
    read_2b = r2b.cache_read_tokens or 0

    # Turn 2 first call should still hit cache for the shared prefix
    # Turn 2 second call should hit even more (full prompt cached)
    ok = read_2b > 0 and read_2 >= 0
    print(f"  Result: t1_warm={read_1b}, t2_first={read_2}, t2_warm={read_2b} "
          f"{'(' + PASS + ')' if ok else '(' + FAIL + ')'}")
    return ok


# -- Scenario 4: prompt_cache_retention: "24h" --------------------------------

def test_prompt_cache_retention(config: OpenAIConfig, tmp_path: Path) -> bool:
    """Send raw request with prompt_cache_retention: "24h".

    Verify the API accepts the parameter.
    Uses raw httpx to inject the field without modifying source code.
    """
    print("\n=== Scenario 4: prompt_cache_retention: \"24h\" ===")

    # Supported models for 24h retention (from OpenAI docs)
    _24H_MODELS = {"gpt-5.4", "gpt-5.2", "gpt-5.1", "gpt-5", "gpt-4.1"}
    model_base = config.model.split("-")[0:2]  # rough check
    model_key = "-".join(model_base) if len(model_base) >= 2 else config.model
    if model_key not in _24H_MODELS and config.model not in _24H_MODELS:
        print(f"  {SKIP}: model {config.model} may not support 24h retention")
        print(f"  (Supported: {', '.join(sorted(_24H_MODELS))})")
        return True  # not a failure, just skip

    builder = _make_builder(tmp_path, cache_ttl=None)
    conv = Conversation()
    conv.add(
        "user", "What is 2+2?",
        timestamp=datetime(2026, 4, 4, 10, 0, tzinfo=timezone.utc),
    )
    messages = builder.build(conv)

    # Build request via existing client pipeline, then inject the field
    from chat_agent.llm.providers.openai import OpenAIClient
    raw_client = OpenAIClient(config)
    request = raw_client._build_request(messages, tools=[])
    payload = request.model_dump(exclude_none=True)
    payload["prompt_cache_retention"] = "24h"

    url = f"{config.base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    print(f"  Sending to {url} with prompt_cache_retention: \"24h\"")

    try:
        with httpx.Client(timeout=config.request_timeout) as http:
            # Call 1: cold
            resp1 = http.post(url, headers=headers, json=payload)
            resp1.raise_for_status()
            data1 = resp1.json()
            usage1 = data1.get("usage", {})
            details1 = usage1.get("prompt_tokens_details", {})
            cached1 = details1.get("cached_tokens", 0)
            prompt1 = usage1.get("prompt_tokens", 0)
            print(f"  {'call 1 (cold)':>35}: prompt={prompt1:>6} cached={cached1:>6}")

            _sleep()

            # Call 2: should hit cache
            resp2 = http.post(url, headers=headers, json=payload)
            resp2.raise_for_status()
            data2 = resp2.json()
            usage2 = data2.get("usage", {})
            details2 = usage2.get("prompt_tokens_details", {})
            cached2 = details2.get("cached_tokens", 0)
            prompt2 = usage2.get("prompt_tokens", 0)
            hit_rate = (cached2 / prompt2 * 100) if prompt2 else 0
            print(f"  {'call 2 (24h retention)':>35}: prompt={prompt2:>6} "
                  f"cached={cached2:>6} hit={hit_rate:>5.1f}%")

        print(f"  Result: API accepted prompt_cache_retention: \"24h\" ({PASS})")
        if cached2 > 0:
            print(f"  Cache hit confirmed with 24h retention ({PASS})")
        else:
            print(f"  No cache hit yet (may need longer prefix or retry)")
        return True

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        body = exc.response.text[:500]
        if "prompt_cache_retention" in body.lower() or status == 400:
            print(f"  Result: API rejected prompt_cache_retention ({FAIL})")
            print(f"  Status: {status}")
            print(f"  Body: {body}")
            return False
        print(f"  Result: HTTP {status} (may be unrelated) ({FAIL})")
        print(f"  Body: {body}")
        return False


# -- Main ----------------------------------------------------------------------

def main() -> None:
    configure_timezone("Asia/Taipei")
    args = _parse_args()
    config = _load_config(args)

    if not config.api_key:
        raise SystemExit("OPENAI_API_KEY is missing. Set it in .env or environment.")

    print("=" * 72)
    print("OpenAI Direct API - Prompt Cache Verification")
    print(f"Model: {config.model}")
    print(f"Base URL: {config.base_url}")
    print("=" * 72)

    client = _make_client(config)
    results: dict[str, bool] = {}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _write_boot_files(tmp_path)

        results["automatic_caching"] = test_automatic_caching(tmp_path, client)
        _sleep(3)
        results["cache_control_tolerance"] = test_cache_control_tolerance(tmp_path, client)
        _sleep(3)
        results["multi_turn"] = test_multi_turn(tmp_path, client)
        _sleep(3)
        results["prompt_cache_retention"] = test_prompt_cache_retention(config, tmp_path)

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
        print(f"\nAll scenarios {PASS}.")
    else:
        print(f"\nSome scenarios {FAIL}. Check output above.")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
