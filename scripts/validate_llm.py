#!/usr/bin/env python3
"""Validate all LLM configs by loading and optionally sending test requests."""

import sys
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

from chat_agent.core.config import CFGS_DIR, resolve_llm_config
from chat_agent.core.schema import OllamaConfig
from chat_agent.llm.factory import create_client
from chat_agent.llm.schema import Message


def main() -> None:
    llm_dir = CFGS_DIR / "llm"
    yaml_files = sorted(llm_dir.rglob("*.yaml"))

    if not yaml_files:
        print("No YAML files found in cfgs/llm/")
        sys.exit(1)

    results: list[tuple[str, str]] = []
    send_requests = "--live" in sys.argv

    for yaml_path in yaml_files:
        rel = yaml_path.relative_to(CFGS_DIR)
        label = str(rel)

        # Load and validate config
        try:
            config = resolve_llm_config(str(rel))
        except Exception as e:
            results.append((label, f"LOAD FAIL: {e}"))
            continue

        # Check if API key is available (skip live tests if missing)
        has_key = True
        if not isinstance(config, OllamaConfig):
            if not getattr(config, "api_key", None):
                has_key = False

        if not send_requests or not has_key:
            results.append((label, "CONFIG OK" + (" (no key)" if not has_key else "")))
            continue

        # Live test: simple chat
        try:
            client = create_client(config, timeout_retries=1)
            reply = client.chat([
                Message(role="user", content="Say 'hello' and nothing else"),
            ])
            if not reply.strip():
                results.append((label, "CHAT FAIL: empty reply"))
                continue
        except Exception as e:
            results.append((label, f"CHAT FAIL: {e}"))
            continue

        # Live test: structured output
        schema = {
            "type": "object",
            "properties": {
                "greeting": {"type": "string"},
            },
            "required": ["greeting"],
            "additionalProperties": False,
        }
        try:
            reply = client.chat(
                [Message(role="user", content="Return a JSON with greeting='hello'")],
                response_schema=schema,
            )
            if "hello" not in reply.lower():
                results.append((label, f"SCHEMA WARN: unexpected reply: {reply[:80]}"))
                continue
        except Exception as e:
            results.append((label, f"SCHEMA FAIL: {e}"))
            continue

        results.append((label, "PASS"))

    # Print results
    max_label = max(len(r[0]) for r in results)
    failed = 0
    for label, status in results:
        marker = "FAIL" if "FAIL" in status else ""
        if marker:
            failed += 1
        print(f"  {label:<{max_label}}  {status}")

    print(f"\n{len(results)} configs checked, {failed} failed")
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
