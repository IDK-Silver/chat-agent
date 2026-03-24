#!/usr/bin/env python3
"""Verify Claude Code prompt-cache behavior through the native local proxy.

This script uses:
- ``ContextBuilder`` from the current repo
- ``run_stage1_information_gathering()`` / ``run_stage2_brain_planning()``
- ``ClaudeCodeClient`` via ``resolve_llm_config()`` + ``create_client()``
- the native ``claude_code_proxy`` service at ``http://127.0.0.1:4142``

It runs the actual staged flow twice:
1. First round warms the exact cache branch.
2. Second round should keep Stage 1 / Stage 2 above 90% if cache parity is healthy.

If the local proxy is not already running, the script will start a temporary one
and shut it down on exit.

Usage:
    uv run python scripts/verify_claude_code_cache_behavior.py
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

load_dotenv()

from chat_agent.agent.responder import _prepare_turn_call_messages
from chat_agent.agent.staged_planning import (
    run_stage1_information_gathering,
    run_stage2_brain_planning,
)
from chat_agent.context.builder import ContextBuilder
from chat_agent.context.conversation import Conversation
from chat_agent.core.config import resolve_llm_config
from chat_agent.core.schema import ClaudeCodeConfig
from chat_agent.llm.factory import create_client
from chat_agent.llm.schema import LLMResponse, ToolDefinition, ToolParameter
from chat_agent.timezone_utils import configure as configure_timezone
from chat_agent.tools.registry import ToolResult

MODEL_CFG = resolve_llm_config("llm/claude_code/claude-haiku-4.5/thinking.yaml")
if not isinstance(MODEL_CFG, ClaudeCodeConfig):
    raise SystemExit("Expected ClaudeCodeConfig for Claude Haiku 4.5.")

BASE_URL = MODEL_CFG.base_url.rstrip("/")
HEALTH_URL = f"{BASE_URL}/health"
CACHE_TTL = "1h"

SYSTEM_FILLER = "Reference dossier. " + " ".join(
    f"Record {i}: habitat sector {i % 17}, observed depth {i * 3}m, "
    f"population estimate {i * 41}, first catalogued {1900 + i}."
    for i in range(1, 240)
)
BOOT_FILLER = "Core rules appendix. " + " ".join(
    f"Appendix {i}: policy {i} remains in force unless superseded by event {i + 7}."
    for i in range(1, 180)
)
HISTORY = "Prior conversation digest. " + " ".join(
    f"Exchange {i}: reminder thread {i} stays open until task {i + 3} is confirmed complete."
    for i in range(1, 120)
)


class RecordingClient:
    """Record each live Claude Code request and response usage."""

    def __init__(self, base):
        self.base = base
        self.calls: list[dict[str, object]] = []

    def chat_with_tools(self, messages, tools, temperature=None):
        response = self.base.chat_with_tools(messages, tools, temperature=temperature)
        self.calls.append(
            {
                "messages": list(messages),
                "tools": [tool.name for tool in tools],
                "response": response,
            }
        )
        return response


class ProbeConsole:
    """Minimal console surface for staged-planning helpers."""

    debug = False
    show_tool_use = False

    def spinner(self, *args, **kwargs):
        return nullcontext()

    def print_tool_call(self, tool_call):
        return None

    def print_tool_result(self, tool_call, content):
        return None

    def print_warning(self, *args, **kwargs):
        return None

    def print_debug(self, *args, **kwargs):
        return None


class ProbeRegistry:
    """Deterministic read-only tool results for live cache verification."""

    def has_tool(self, name: str) -> bool:
        return name in {"memory_search", "read_file", "web_search", "web_fetch"}

    def execute(self, tool_call) -> ToolResult:
        if tool_call.name == "memory_search":
            return ToolResult("memory_search result: user likes concise answers")
        if tool_call.name == "read_file":
            return ToolResult(
                '<file path="memory/agent/context.md">Project brief: keep plans short.</file>'
            )
        if tool_call.name == "web_search":
            return ToolResult("web_search result: no current external fact needed")
        if tool_call.name == "web_fetch":
            return ToolResult("web_fetch result: no fetch needed")
        return ToolResult(f"unsupported {tool_call.name}", is_error=True)


def _make_client():
    return create_client(
        MODEL_CFG,
        transient_retries=0,
        request_timeout=MODEL_CFG.request_timeout,
        rate_limit_retries=0,
        retry_label="verify_claude_code_cache_behavior",
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
        provider="claude_code",
        cache_ttl=CACHE_TTL,
    )
    builder.reload_boot_files()
    return builder


def _make_prepared_messages(builder: ContextBuilder) -> list:
    conv = Conversation()
    conv.add(
        "user",
        "Earlier question. " + HISTORY,
        timestamp=datetime(2026, 3, 24, 0, 30, tzinfo=timezone.utc),
    )
    conv.add(
        "assistant",
        "Earlier answer. " + HISTORY,
        timestamp=datetime(2026, 3, 24, 0, 31, tzinfo=timezone.utc),
    )
    conv.add(
        "user",
        "Please think first. If needed you may inspect files or the web, then produce a short plan.",
        timestamp=datetime(2026, 3, 24, 0, 50, tzinfo=timezone.utc),
        metadata={"turn_processing_started_at": "2026-03-24T08:50:00+08:00"},
    )
    return _prepare_turn_call_messages(builder.build(conv))


def _build_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="memory_search",
            description="search memory",
            parameters={"query": ToolParameter(type="string", description="query")},
            required=["query"],
        ),
        ToolDefinition(
            name="read_file",
            description="read file",
            parameters={"path": ToolParameter(type="string", description="path")},
            required=["path"],
        ),
        ToolDefinition(
            name="web_search",
            description="search web",
            parameters={"query": ToolParameter(type="string", description="query")},
            required=["query"],
        ),
        ToolDefinition(
            name="web_fetch",
            description="fetch web",
            parameters={"url": ToolParameter(type="string", description="url")},
            required=["url"],
        ),
        ToolDefinition(
            name="send_message",
            description="send message",
            parameters={"body": ToolParameter(type="string", description="body")},
            required=["body"],
        ),
        ToolDefinition(
            name="memory_edit",
            description="edit memory",
            parameters={"target": ToolParameter(type="string", description="target")},
            required=["target"],
        ),
    ]


def _is_proxy_healthy() -> bool:
    try:
        response = httpx.get(HEALTH_URL, timeout=2.0)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _wait_for_proxy(timeout_seconds: float) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _is_proxy_healthy():
            return True
        time.sleep(0.25)
    return False


@contextmanager
def _managed_proxy():
    if _is_proxy_healthy():
        print(f"Proxy already running at {BASE_URL}")
        yield
        return

    parsed = urlparse(BASE_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 4142
    command = [
        sys.executable,
        "-m",
        "claude_code_proxy",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_for_proxy(timeout_seconds=15.0):
            raise RuntimeError(f"Timed out waiting for Claude Code proxy at {BASE_URL}")
        print(f"Started temporary proxy at {BASE_URL}")
        yield
    finally:
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)


def _print_call(label: str, response: LLMResponse) -> None:
    prompt = response.prompt_tokens or 0
    cached = response.cache_read_tokens or 0
    written = response.cache_write_tokens or 0
    ratio = (cached / prompt * 100) if prompt else 0.0
    print(
        f"{label:>24}: prompt={prompt:>6} "
        f"cache_read={cached:>6} cache_write={written:>6} "
        f"hit={ratio:>5.1f}% finish={response.finish_reason}"
    )


def _sleep() -> None:
    time.sleep(2.0)


def run_live_staged_flow(client: RecordingClient, builder: ContextBuilder) -> None:
    console = ProbeConsole()
    registry = ProbeRegistry()
    tools = _build_tools()

    for round_idx in (1, 2):
        prepared = _make_prepared_messages(builder)

        stage1_start = len(client.calls)
        stage1 = run_stage1_information_gathering(
            client=client,
            messages=prepared,
            all_tools=tools,
            registry=registry,
            console=console,
            max_iterations=2,
            skip_memory_search_gate=True,
        )
        stage1_calls = client.calls[stage1_start:]
        _sleep()

        stage2_start = len(client.calls)
        stage2 = run_stage2_brain_planning(
            client=client,
            messages=prepared,
            stage1=stage1,
            all_tools=tools,
            registry=registry,
            console=console,
            send_message_batch_guidance=True,
            max_iterations=3,
        )
        stage2_calls = client.calls[stage2_start:]

        print(
            f"\nRound {round_idx}: "
            f"stage1_findings={len(stage1.findings_text)} "
            f"stage2_plan={bool(stage2 and stage2.plan_text.strip())}"
        )

        for call_idx, call in enumerate(stage1_calls, 1):
            response = call["response"]
            assert isinstance(response, LLMResponse)
            _print_call(f"round{round_idx} stage1.{call_idx}", response)
        for call_idx, call in enumerate(stage2_calls, 1):
            response = call["response"]
            assert isinstance(response, LLMResponse)
            _print_call(f"round{round_idx} stage2.{call_idx}", response)

        _sleep()


def main() -> None:
    configure_timezone("Asia/Taipei")
    print("=" * 72)
    print("Claude Code Prompt Cache Verification With Native Local Proxy")
    print(f"Model: {MODEL_CFG.model}")
    print(f"Proxy: {BASE_URL}")
    print("=" * 72)

    with _managed_proxy():
        client = RecordingClient(_make_client())
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_boot_files(tmp_path)
            builder = _make_builder(tmp_path)
            run_live_staged_flow(client, builder)

    print("\nInterpretation:")
    print("- Round 1 first call may be a cold or partial cache write for the exact branch.")
    print("- After the first branch write, Stage 1 / Stage 2 should stay around or above 90%.")
    print("- If later rounds still fall back to 10%~20%, tools/messages parity is still broken.")


if __name__ == "__main__":
    main()
