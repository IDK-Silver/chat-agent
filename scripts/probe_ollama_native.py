#!/usr/bin/env python3
"""Probe Ollama native cloud profiles with live requests."""

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

from chat_agent.core.config import resolve_llm_config
from chat_agent.core.schema import OllamaNativeConfig
from chat_agent.llm.providers.ollama_native import OllamaNativeClient
from chat_agent.llm.schema import ContentPart, Message, ToolDefinition, ToolParameter


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a live probe request through OllamaNativeClient.",
    )
    parser.add_argument(
        "--profile",
        required=True,
        help="Path relative to cfgs/, for example llm/ollama/kimi-k2.5-cloud/thinking.yaml",
    )
    parser.add_argument(
        "--prompt",
        default="請只回答 ok。",
        help="User prompt for the probe request.",
    )
    parser.add_argument(
        "--system",
        default=None,
        help="Optional system prompt.",
    )
    parser.add_argument(
        "--image",
        default=None,
        help="Optional local image path to send with the user prompt.",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Request a simple JSON schema response with an answer field.",
    )
    parser.add_argument(
        "--tools",
        action="store_true",
        help="Include a simple echo_text tool and use chat_with_tools().",
    )
    parser.add_argument(
        "--force-tool",
        action="store_true",
        help="Add a system instruction that strongly pushes the model to call the tool.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Override profile max_tokens. Project validation requires >= 1.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Override profile temperature. Project validation requires >= 0.",
    )
    parser.add_argument(
        "--dump-request",
        action="store_true",
        help="Print the full native request payload instead of the compact summary.",
    )
    return parser.parse_args()


def _load_client(
    profile: str,
    *,
    max_tokens: int | None,
    temperature: float | None,
) -> tuple[OllamaNativeConfig, OllamaNativeClient]:
    config = resolve_llm_config(profile)
    if not isinstance(config, OllamaNativeConfig):
        raise SystemExit(f"{profile} is not an Ollama native profile")

    updates: dict[str, Any] = {}
    if max_tokens is not None:
        updates["max_tokens"] = max_tokens
    if temperature is not None:
        updates["temperature"] = temperature
    if updates:
        config = config.model_copy(update=updates)

    client = config.create_client()
    if not isinstance(client, OllamaNativeClient):
        raise SystemExit("Resolved config did not create OllamaNativeClient")
    return config, client


def _read_image_part(image_path: str) -> ContentPart:
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"Image not found: {path}")

    suffix = path.suffix.lower()
    media_type_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    media_type = media_type_map.get(suffix)
    if media_type is None:
        raise SystemExit(f"Unsupported image extension for probe: {suffix}")

    return ContentPart(
        type="image",
        media_type=media_type,
        data=base64.b64encode(path.read_bytes()).decode("ascii"),
    )


def _build_messages(args: argparse.Namespace) -> list[Message]:
    messages: list[Message] = []
    system_parts: list[str] = []
    if args.system:
        system_parts.append(args.system)
    if args.force_tool:
        system_parts.append(
            "如果有工具可以直接完成任務，你必須先呼叫工具，不要直接回答。"
        )
    if system_parts:
        messages.append(Message(role="system", content="\n\n".join(system_parts)))

    if args.image:
        parts = [
            ContentPart(type="text", text=args.prompt),
            _read_image_part(args.image),
        ]
        messages.append(Message(role="user", content=parts))
    else:
        messages.append(Message(role="user", content=args.prompt))
    return messages


def _build_schema(enabled: bool) -> dict[str, Any] | None:
    if not enabled:
        return None
    return {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
        },
        "required": ["answer"],
        "additionalProperties": False,
    }


def _build_tools(enabled: bool) -> list[ToolDefinition]:
    if not enabled:
        return []
    return [
        ToolDefinition(
            name="echo_text",
            description="Echoes the provided text.",
            parameters={
                "text": ToolParameter(type="string", description="text to echo"),
            },
            required=["text"],
        )
    ]


def _summarize_payload(payload: dict[str, Any], url: str) -> dict[str, Any]:
    return {
        "url": url,
        "model": payload.get("model"),
        "stream": payload.get("stream"),
        "think": payload.get("think"),
        "has_tools": bool(payload.get("tools")),
        "tool_names": [
            tool["function"]["name"]
            for tool in payload.get("tools", [])
            if tool.get("function")
        ],
        "format_kind": type(payload.get("format")).__name__ if "format" in payload else None,
        "options": payload.get("options"),
        "message_roles": [message.get("role") for message in payload.get("messages", [])],
        "has_any_images": any(
            bool(message.get("images")) for message in payload.get("messages", [])
        ),
    }


def main() -> None:
    args = _parse_args()
    config, client = _load_client(
        args.profile,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
    )
    messages = _build_messages(args)
    schema = _build_schema(args.schema)
    tools = _build_tools(args.tools)

    if tools:
        request = client._build_request(messages, tools=tools, temperature=args.temperature)
    else:
        request = client._build_request(
            messages,
            response_schema=schema,
            temperature=args.temperature,
        )

    payload = request.model_dump(exclude_none=True)
    print(
        json.dumps(
            {
                "profile": args.profile,
                "config": {
                    "model": config.model,
                    "vision": config.vision,
                    "thinking_mode": config.thinking.mode,
                    "max_tokens": config.max_tokens,
                    "temperature": config.temperature,
                },
                "request": payload if args.dump_request else _summarize_payload(payload, client.chat_url),
            },
            ensure_ascii=False,
            indent=2 if args.dump_request else None,
        )
    )

    if tools:
        response = client.chat_with_tools(messages, tools, temperature=args.temperature)
        result = {
            "content": response.content,
            "reasoning_present": bool(response.reasoning_content),
            "tool_calls": [
                {"name": tool_call.name, "arguments": tool_call.arguments}
                for tool_call in response.tool_calls
            ],
            "finish_reason": response.finish_reason,
            "usage": {
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "total_tokens": response.total_tokens,
                "usage_available": response.usage_available,
            },
        }
    else:
        data = client._do_post(request)
        result = {
            "content": data.get("message", {}).get("content"),
            "thinking": data.get("message", {}).get("thinking"),
            "tool_calls": data.get("message", {}).get("tool_calls"),
            "done_reason": data.get("done_reason"),
            "prompt_eval_count": data.get("prompt_eval_count"),
            "eval_count": data.get("eval_count"),
        }
    print(json.dumps({"response": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
