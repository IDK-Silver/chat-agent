"""Tests for httpx-based web_fetch tool."""

from pathlib import Path
import socket

import httpx

from chat_agent.agent.core import setup_tools
from chat_agent.agent.staged_planning import build_stage1_tools
from chat_agent.core.schema import ToolsConfig
from chat_agent.tools.builtin.web_fetch import (
    WEB_FETCH_DEFINITION,
    create_web_fetch,
)
from chat_agent.tools.builtin.web_search import WEB_SEARCH_DEFINITION


class _FakeStreamResponse:
    def __init__(
        self,
        *,
        body: bytes,
        url: str,
        content_type: str,
        status_code: int = 200,
    ) -> None:
        self._body = body
        self.url = url
        self.status_code = status_code
        self.headers = {"content-type": content_type, "content-length": str(len(body))}
        self.request = httpx.Request("GET", url)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def iter_bytes(self):
        yield self._body


class _FakeClient:
    def __init__(
        self,
        *,
        response: _FakeStreamResponse | Exception,
        calls: list[dict],
        timeout: float,
        follow_redirects: bool,
        headers: dict,
    ) -> None:
        self._response = response
        self._calls = calls
        self._timeout = timeout
        self._follow_redirects = follow_redirects
        self._headers = headers

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def stream(self, method: str, url: str):
        self._calls.append(
            {
                "method": method,
                "url": url,
                "timeout": self._timeout,
                "follow_redirects": self._follow_redirects,
                "headers": self._headers,
            }
        )
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _patch_public_dns(monkeypatch) -> None:
    monkeypatch.setattr(
        "chat_agent.tools.builtin.web_fetch.socket.getaddrinfo",
        lambda host, port, type=socket.SOCK_STREAM: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )


class TestWebFetchDefinition:
    def test_name_and_params(self):
        assert WEB_FETCH_DEFINITION.name == "web_fetch"
        assert WEB_FETCH_DEFINITION.required == ["url"]
        assert "max_chars" in WEB_FETCH_DEFINITION.parameters


class TestCreateWebFetch:
    def test_fetches_html_and_formats_metadata(self, monkeypatch):
        _patch_public_dns(monkeypatch)
        calls: list[dict] = []
        response = _FakeStreamResponse(
            body=(
                b"<html><head><title>Example Docs</title>"
                b'<meta name="description" content="Quick overview."></head>'
                b"<body><main><h1>Hello</h1><p>world.</p></main></body></html>"
            ),
            url="https://example.com/final",
            content_type="text/html; charset=utf-8",
        )

        monkeypatch.setattr(
            "chat_agent.tools.builtin.web_fetch.httpx.Client",
            lambda timeout, follow_redirects, headers: _FakeClient(
                response=response,
                calls=calls,
                timeout=timeout,
                follow_redirects=follow_redirects,
                headers=headers,
            ),
        )

        tool = create_web_fetch(
            timeout=7.5,
            default_max_chars=500,
            max_response_chars=300,
            max_response_bytes=4096,
            user_agent="test-agent",
        )

        output = tool(url="https://example.com/docs", max_chars=999)

        assert output.startswith("Fetched: https://example.com/docs")
        assert "Final URL: https://example.com/final" in output
        assert "Status: 200" in output
        assert "Content-Type: text/html" in output
        assert "Title: Example Docs" in output
        assert "Description: Quick overview." in output
        assert "Hello world." in output
        assert "Truncated: yes" not in output
        assert calls == [
            {
                "method": "GET",
                "url": "https://example.com/docs",
                "timeout": 7.5,
                "follow_redirects": True,
                "headers": {
                    "User-Agent": "test-agent",
                    "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.1",
                },
            }
        ]

    def test_fetches_json_and_truncates_when_requested(self, monkeypatch):
        _patch_public_dns(monkeypatch)
        calls: list[dict] = []
        response = _FakeStreamResponse(
            body=b'{"name":"chat-agent","items":[1,2,3,4]}',
            url="https://api.example.com/data",
            content_type="application/json",
        )

        monkeypatch.setattr(
            "chat_agent.tools.builtin.web_fetch.httpx.Client",
            lambda timeout, follow_redirects, headers: _FakeClient(
                response=response,
                calls=calls,
                timeout=timeout,
                follow_redirects=follow_redirects,
                headers=headers,
            ),
        )

        tool = create_web_fetch(default_max_chars=40, max_response_chars=40)

        output = tool(url="https://api.example.com/data")

        assert "Content-Type: application/json" in output
        assert "{" in output
        assert "Truncated: yes" in output

    def test_returns_validation_errors(self, monkeypatch):
        _patch_public_dns(monkeypatch)
        tool = create_web_fetch()

        assert tool(url="") == "Error: url is required."
        assert tool(url="ftp://example.com") == "Error: url must use http or https."
        assert tool(url="https://user:pass@example.com") == (  # pragma: allowlist secret
            "Error: url must not include credentials."
        )
        assert tool(url="https://127.0.0.1/test") == (
            "Error: private or local addresses are not allowed."
        )
        assert tool(url="https://example.com", max_chars=10) == (
            "Error: max_chars must be an integer >= 200."
        )

    def test_handles_http_errors(self, monkeypatch):
        _patch_public_dns(monkeypatch)
        calls: list[dict] = []

        monkeypatch.setattr(
            "chat_agent.tools.builtin.web_fetch.httpx.Client",
            lambda timeout, follow_redirects, headers: _FakeClient(
                response=httpx.TimeoutException("timed out"),
                calls=calls,
                timeout=timeout,
                follow_redirects=follow_redirects,
                headers=headers,
            ),
        )
        tool = create_web_fetch()
        assert tool(url="https://example.com") == "Error: Fetch timed out."

        response = _FakeStreamResponse(
            body=b"not found",
            url="https://example.com/missing",
            content_type="text/plain",
            status_code=404,
        )
        monkeypatch.setattr(
            "chat_agent.tools.builtin.web_fetch.httpx.Client",
            lambda timeout, follow_redirects, headers: _FakeClient(
                response=response,
                calls=calls,
                timeout=timeout,
                follow_redirects=follow_redirects,
                headers=headers,
            ),
        )
        assert tool(url="https://example.com/missing") == "Error: Fetch failed (404)."

    def test_rejects_responses_over_size_limit(self, monkeypatch):
        _patch_public_dns(monkeypatch)
        calls: list[dict] = []
        response = _FakeStreamResponse(
            body=b"x" * 20,
            url="https://example.com/big",
            content_type="text/plain",
        )

        monkeypatch.setattr(
            "chat_agent.tools.builtin.web_fetch.httpx.Client",
            lambda timeout, follow_redirects, headers: _FakeClient(
                response=response,
                calls=calls,
                timeout=timeout,
                follow_redirects=follow_redirects,
                headers=headers,
            ),
        )

        tool = create_web_fetch(max_response_bytes=8)

        assert tool(url="https://example.com/big") == (
            "Error: Response too large (20 bytes > limit 8)."
        )


class TestWebFetchWiring:
    def test_setup_tools_skips_web_fetch_when_disabled(self, tmp_path: Path):
        config = ToolsConfig.model_validate({"allowed_paths": []})

        registry, _, _ = setup_tools(config, tmp_path)

        assert not registry.has_tool("web_fetch")

    def test_setup_tools_registers_web_fetch_when_enabled(self, tmp_path: Path):
        config = ToolsConfig.model_validate(
            {"allowed_paths": [], "web_fetch": {"enabled": True}}
        )

        registry, _, _ = setup_tools(config, tmp_path)

        assert registry.has_tool("web_fetch")

    def test_stage1_whitelist_includes_web_fetch(self):
        tools = build_stage1_tools([WEB_SEARCH_DEFINITION, WEB_FETCH_DEFINITION])

        assert [tool.name for tool in tools] == ["web_search", "web_fetch"]
