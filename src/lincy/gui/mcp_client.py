"""Minimal MCP client over stdio for the local OpenComputerUse server.

Speaks JSON-RPC 2.0 with newline-delimited framing (the framing used by the
MCP stdio transport). Only the three methods the GUI stack needs are
implemented: initialize, tools/list, tools/call.
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2025-06-18"


class MCPError(RuntimeError):
    """Transport or protocol failure talking to the MCP server."""


class MCPToolImage:
    """Base64 image block returned by a tool call."""

    def __init__(self, data: str, mime_type: str):
        self.data = data
        self.mime_type = mime_type


class MCPStdioClient:
    """Client bound to one spawned MCP server subprocess."""

    def __init__(self, command: list[str], timeout: float = 60.0):
        try:
            self._proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as e:
            raise MCPError(f"failed to spawn MCP server {command[0]}: {e}") from e
        self._timeout = timeout
        self._next_id = 0
        self._incoming: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_tail: list[str] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    # --- transport ---

    def _pump_stdout(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._incoming.put(json.loads(line))
            except json.JSONDecodeError:
                logger.debug("MCP non-JSON stdout: %s", line[:200])

    def _pump_stderr(self) -> None:
        assert self._proc.stderr is not None
        for line in self._proc.stderr:
            self._stderr_tail.append(line.rstrip()[:300])
            del self._stderr_tail[:-10]

    def _send(self, payload: dict[str, Any]) -> None:
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise MCPError(f"MCP server pipe closed: {e}; {self._stderr()}") from e

    def _stderr(self) -> str:
        return "stderr: " + " | ".join(self._stderr_tail[-5:])

    def _request(
        self, method: str, params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
            if params is not None:
                msg["params"] = params
            self._send(msg)
            deadline = time.monotonic() + (timeout or self._timeout)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MCPError(f"timeout on {method}; {self._stderr()}")
                try:
                    incoming = self._incoming.get(timeout=min(remaining, 1.0))
                except queue.Empty:
                    if self._proc.poll() is not None:
                        raise MCPError(
                            f"MCP server exited rc={self._proc.returncode}; "
                            + self._stderr()
                        )
                    continue
                if incoming.get("id") != rid:
                    continue  # notification or stale response
                if "error" in incoming:
                    raise MCPError(json.dumps(incoming["error"], ensure_ascii=False))
                return incoming.get("result", {})

    def _notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    # --- MCP methods ---

    def initialize(self) -> dict[str, Any]:
        result = self._request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "chat-agent", "version": "1.0"},
            },
        )
        self._notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        return self._request("tools/list").get("tools", [])

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> tuple[str, list[MCPToolImage], bool]:
        """Call a tool; returns (joined_text, images, is_error)."""
        result = self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        )
        texts: list[str] = []
        images: list[MCPToolImage] = []
        for block in result.get("content", []):
            if block.get("type") == "text" and block.get("text"):
                texts.append(block["text"])
            elif block.get("type") == "image" and block.get("data"):
                images.append(
                    MCPToolImage(block["data"], block.get("mimeType", "image/png"))
                )
        return "\n".join(texts), images, bool(result.get("isError"))

    def close(self) -> None:
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except (subprocess.TimeoutExpired, OSError):
            self._proc.kill()

    def __enter__(self) -> "MCPStdioClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
