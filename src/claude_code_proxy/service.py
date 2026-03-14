"""Upstream Anthropic transport for the native Claude Code proxy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
import httpx

from chat_agent.llm.schema import ClaudeCodeRequest

from .auth import (
    ClaudeCodeCredentialLoader,
    ClaudeCodeTokenStore,
    DEFAULT_CLAUDE_CODE_OAUTH_TOKEN_URL,
    StoredClaudeCodeToken,
    is_token_fresh,
    normalize_bearer_token,
)
from .settings import ClaudeCodeProxySettings


class ClaudeCodeUpstreamError(RuntimeError):
    """Wrap upstream HTTP errors while preserving raw response payload."""

    def __init__(self, *, status_code: int, body: bytes, media_type: str):
        super().__init__(body.decode("utf-8", errors="replace"))
        self.status_code = status_code
        self.body = body
        self.media_type = media_type


class ClaudeCodeTokenManager:
    """Load, cache, and refresh Claude Code OAuth tokens."""

    def __init__(self, settings: ClaudeCodeProxySettings):
        self._settings = settings
        self._token_store = ClaudeCodeTokenStore(settings.token_path)
        self._credentials = ClaudeCodeCredentialLoader(path=settings.credentials_path)
        self._access_token: str | None = None
        self._expires_at: datetime | None = None
        self._lock = anyio.Lock()

    async def get_token(self) -> str:
        if self._settings.access_token:
            return normalize_bearer_token(self._settings.access_token)

        async with self._lock:
            if (
                self._access_token is not None
                and self._expires_at is not None
                and self._expires_at.timestamp() - 60 > datetime.now(tz=UTC).timestamp()
            ):
                return self._access_token

            errors: list[str] = []
            stored = self._load_store(errors)
            if stored is not None and is_token_fresh(stored):
                return self._cache_and_return(stored)

            if stored is not None and stored.refresh_token:
                try:
                    refreshed = await self._refresh(stored.refresh_token)
                    self._token_store.save(refreshed)
                    return self._cache_and_return(refreshed)
                except Exception as exc:
                    errors.append(f"stored refresh failed: {exc}")

            imported = self._load_credentials(errors)
            if imported is not None and is_token_fresh(imported):
                self._token_store.save(imported)
                return self._cache_and_return(imported)

            if imported is not None and imported.refresh_token:
                try:
                    refreshed = await self._refresh(imported.refresh_token)
                    self._token_store.save(refreshed)
                    return self._cache_and_return(refreshed)
                except Exception as exc:
                    errors.append(f"credential refresh failed: {exc}")

            detail = "; ".join(errors) if errors else "no token source available"
            raise RuntimeError(
                "Claude Code token is required. Set CLAUDE_CODE_PROXY_ACCESS_TOKEN, "
                "run `uv run claude-code-proxy login`, run "
                "`uv run claude-code-proxy login --from-claude-code`, "
                f"or enable Claude Code fallback. ({detail})"
            )

    def _cache_and_return(self, token: StoredClaudeCodeToken) -> str:
        self._access_token = token.access_token
        self._expires_at = token.expires_at
        return token.access_token

    def _load_store(self, errors: list[str]) -> StoredClaudeCodeToken | None:
        try:
            return self._token_store.load()
        except ValueError as exc:
            errors.append(str(exc))
            return None

    def _load_credentials(self, errors: list[str]) -> StoredClaudeCodeToken | None:
        if not self._settings.allow_claude_code_fallback:
            return None
        try:
            return self._credentials.load()
        except ValueError as exc:
            errors.append(str(exc))
            return None

    async def _refresh(self, refresh_token: str) -> StoredClaudeCodeToken:
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._settings.oauth_client_id,
        }
        async with httpx.AsyncClient(timeout=self._settings.request_timeout) as client:
            response = await client.post(
                DEFAULT_CLAUDE_CODE_OAUTH_TOKEN_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"OAuth refresh failed with status {response.status_code}: {response.text}"
            )
        data = response.json()
        access_token = data.get("access_token")
        expires_in = data.get("expires_in")
        if not isinstance(access_token, str) or not access_token:
            raise RuntimeError("OAuth refresh returned no access_token")
        if not isinstance(expires_in, (int, float)) or expires_in <= 0:
            raise RuntimeError("OAuth refresh returned invalid expires_in")
        next_refresh_token = data.get("refresh_token")
        if next_refresh_token is not None and not isinstance(next_refresh_token, str):
            raise RuntimeError("OAuth refresh returned invalid refresh_token")
        return StoredClaudeCodeToken(
            access_token=access_token,
            refresh_token=next_refresh_token or refresh_token,
            expires_at=datetime.now(tz=UTC) + timedelta(seconds=float(expires_in)),
            source="oauth_refresh",
            client_id=self._settings.oauth_client_id,
            created_at=datetime.now(tz=UTC),
        )


class ClaudeCodeProxyService:
    """Translate local Claude Code requests into upstream Anthropic calls."""

    def __init__(self, settings: ClaudeCodeProxySettings):
        self._settings = settings
        self._tokens = ClaudeCodeTokenManager(settings)

    async def forward_json(self, request: ClaudeCodeRequest) -> tuple[bytes, str]:
        token = await self._tokens.get_token()
        payload = self._build_upstream_request(request)
        async with httpx.AsyncClient(timeout=self._settings.request_timeout) as client:
            response = await client.post(
                f"{self._settings.anthropic_base_url}/v1/messages",
                headers=self._headers(token),
                json=payload,
            )
        if response.status_code >= 400:
            raise ClaudeCodeUpstreamError(
                status_code=response.status_code,
                body=response.content,
                media_type=response.headers.get("content-type", "application/json"),
            )
        return response.content, response.headers.get("content-type", "application/json")

    async def open_stream(
        self,
        request: ClaudeCodeRequest,
    ) -> tuple[httpx.AsyncClient, httpx.Response]:
        token = await self._tokens.get_token()
        payload = self._build_upstream_request(request)
        client = httpx.AsyncClient(timeout=self._settings.request_timeout)
        try:
            upstream_request = client.build_request(
                "POST",
                f"{self._settings.anthropic_base_url}/v1/messages",
                headers=self._headers(token),
                json=payload,
            )
            response = await client.send(upstream_request, stream=True)
            if response.status_code >= 400:
                body = await response.aread()
                await response.aclose()
                await client.aclose()
                raise ClaudeCodeUpstreamError(
                    status_code=response.status_code,
                    body=body,
                    media_type=response.headers.get("content-type", "application/json"),
                )
            return client, response
        except Exception:
            await client.aclose()
            raise

    def _headers(self, token: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-version": self._settings.anthropic_version,
            "Content-Type": "application/json",
            "User-Agent": self._settings.user_agent,
        }
        if self._settings.beta_headers:
            headers["anthropic-beta"] = self._settings.beta_headers
        return headers

    def _build_upstream_request(self, request: ClaudeCodeRequest) -> dict[str, Any]:
        payload = request.model_dump(exclude_none=True, by_alias=True)
        system = self._normalize_system(payload.get("system"))
        payload["system"] = self._prepend_required_prompt(system)
        return payload

    @staticmethod
    def _normalize_system(system: Any) -> list[dict[str, Any]]:
        if system is None:
            return []
        if isinstance(system, str):
            return [{"type": "text", "text": system}]
        if not isinstance(system, list):
            raise ValueError("system must be a string or a list of content blocks")
        normalized: list[dict[str, Any]] = []
        for item in system:
            if isinstance(item, str):
                normalized.append({"type": "text", "text": item})
            elif isinstance(item, dict):
                normalized.append(dict(item))
            else:
                raise ValueError("system block entries must be strings or objects")
        return normalized

    def _prepend_required_prompt(
        self,
        system_blocks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        required = {
            "type": "text",
            "text": self._settings.required_system_prompt,
        }
        if system_blocks:
            first = system_blocks[0]
            if (
                first.get("type") == "text"
                and first.get("text") == self._settings.required_system_prompt
            ):
                return system_blocks
        return [required, *system_blocks]
