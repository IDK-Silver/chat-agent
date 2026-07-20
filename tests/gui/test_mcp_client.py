"""Tests for gui/mcp_client.py against a scripted fake stdio server."""

import sys

import pytest

from lincy.gui.mcp_client import MCPError, MCPStdioClient

FAKE_SERVER = r"""
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    rid = msg.get("id")
    if method == "notifications/initialized":
        continue
    if method == "initialize":
        out = {"jsonrpc": "2.0", "id": rid,
               "result": {"serverInfo": {"name": "fake", "version": "0"}}}
    elif method == "tools/list":
        out = {"jsonrpc": "2.0", "id": rid,
               "result": {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}}
    elif method == "tools/call":
        name = msg["params"]["name"]
        if name == "die":
            sys.exit(3)
        if name == "err":
            out = {"jsonrpc": "2.0", "id": rid,
                   "result": {"isError": True,
                              "content": [{"type": "text", "text": "bad thing"}]}}
        elif name == "img":
            out = {"jsonrpc": "2.0", "id": rid,
                   "result": {"content": [
                       {"type": "text", "text": "state"},
                       {"type": "image", "data": "QUJD", "mimeType": "image/png"},
                   ]}}
        elif name == "rpcerr":
            out = {"jsonrpc": "2.0", "id": rid,
                   "error": {"code": -32000, "message": "nope"}}
        else:
            out = {"jsonrpc": "2.0", "id": rid,
                   "result": {"content": [
                       {"type": "text",
                        "text": json.dumps(msg["params"].get("arguments", {}))},
                   ]}}
    else:
        continue
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
"""


@pytest.fixture
def client():
    c = MCPStdioClient([sys.executable, "-u", "-c", FAKE_SERVER], timeout=10)
    yield c
    c.close()


def test_initialize_and_list_tools(client):
    info = client.initialize()
    assert info["serverInfo"]["name"] == "fake"
    tools = client.list_tools()
    assert tools[0]["name"] == "echo"


def test_call_tool_roundtrips_arguments(client):
    client.initialize()
    text, images, is_error = client.call_tool("echo", {"a": 1, "b": "x"})
    assert '"a": 1' in text
    assert images == []
    assert is_error is False


def test_call_tool_maps_images(client):
    client.initialize()
    text, images, is_error = client.call_tool("img")
    assert text == "state"
    assert len(images) == 1
    assert images[0].data == "QUJD"
    assert images[0].mime_type == "image/png"
    assert is_error is False


def test_is_error_flag_surfaces(client):
    client.initialize()
    text, images, is_error = client.call_tool("err")
    assert is_error is True
    assert "bad thing" in text


def test_jsonrpc_error_raises(client):
    client.initialize()
    with pytest.raises(MCPError, match="nope"):
        client.call_tool("rpcerr")


def test_server_death_raises(client):
    client.initialize()
    with pytest.raises(MCPError, match="exited"):
        client.call_tool("die")


def test_spawn_failure_raises():
    with pytest.raises(MCPError, match="spawn"):
        MCPStdioClient(["/nonexistent/binary-xyz"])
