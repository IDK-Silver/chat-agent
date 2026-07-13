"""Upstream xAI transport for the native SuperGrok OAuth proxy."""

from __future__ import annotations

from typing import Any

import anyio
import httpx

from .auth import (
    GrokAuthError,
    GrokOAuthClient,
    GrokTokenStore,
    StoredGrokToken,
    is_token_fresh,
)
from .settings import GrokProxySettings

ENV_ACCESS_TOKEN_ID = "__env_access_token__"


class GrokUpstreamError(RuntimeError):
    """Wrap upstream HTTP errors while preserving raw response payload."""

    def __init__(self, *, status_code: int, body: bytes, media_type: str):
        super().__init__(body.decode("utf-8", errors="replace"))
        self.status_code = status_code
        self.body = body
        self.media_type = media_type


class GrokTokenUnavailableError(RuntimeError):
    """No usable SuperGrok OAuth token is available."""


class GrokTokenManager:
    """Load, cache, and refresh SuperGrok OAuth tokens."""

    def __init__(self, settings: GrokProxySettings):
        self._settings = settings
        self._store = GrokTokenStore(settings.token_path)
        self._oauth = GrokOAuthClient(
            request_timeout=settings.request_timeout,
            client_id=settings.oauth_client_id,
            scope=settings.oauth_scope,
            discovery_url=settings.oauth_discovery_url,
        )
        self._cached: StoredGrokToken | None = None
        self._lock = anyio.Lock()

    async def acquire(self, *, force_refresh: bool = False) -> str:
        """Return a usable access token, refreshing when needed."""

        if self._settings.access_token:
            return self._settings.access_token

        async with self._lock:
            token = self._cached or self._store.load()
            if token is None:
                raise GrokTokenUnavailableError(
                    "No Grok OAuth token stored. Run `uv run proxy grok login`."
                )
            if not force_refresh and is_token_fresh(
                token, skew_seconds=self._settings.refresh_skew_seconds
            ):
                self._cached = token
                return token.access_token

            refreshed = await anyio.to_thread.run_sync(self._refresh_sync, token)
            self._cached = refreshed
            return refreshed.access_token

    def _refresh_sync(self, token: StoredGrokToken) -> StoredGrokToken:
        tokens = self._oauth.refresh(
            token.refresh_token,
            token_endpoint=token.token_endpoint,
        )
        stored = self._oauth.build_stored_token(
            tokens,
            token_endpoint=token.token_endpoint,
            source="oauth_refresh",
            created_at=token.created_at,
            previous_refresh_token=token.refresh_token,
        )
        self._store.update(stored)
        return stored


# Client-supplied headers that must reach xAI for correct prompt-cache routing.
# Chat Completions: x-grok-conv-id sticky server affinity.
# https://docs.x.ai/developers/advanced-api-usage/prompt-caching/maximizing-cache-hits
_FORWARD_HEADER_NAMES = frozenset({"x-grok-conv-id"})


class GrokProxyService:
    """Forward OpenAI-compatible requests to xAI with OAuth injection."""

    def __init__(self, settings: GrokProxySettings):
        self._settings = settings
        self._tokens = GrokTokenManager(settings)

    async def forward_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[bytes, str, int]:
        """Forward a non-streaming request. Returns (body, media_type, status)."""

        last_error: GrokUpstreamError | None = None
        for attempt in range(2):
            force_refresh = attempt > 0
            try:
                access_token = await self._tokens.acquire(force_refresh=force_refresh)
            except (GrokAuthError, GrokTokenUnavailableError):
                if last_error is not None:
                    raise last_error from None
                raise

            url = self._url(path)
            async with httpx.AsyncClient(timeout=self._settings.request_timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=self._headers(access_token, extra_headers=extra_headers),
                    json=json_body,
                    params=params,
                )
            if response.status_code < 400:
                return (
                    response.content,
                    response.headers.get("content-type", "application/json"),
                    response.status_code,
                )

            error = GrokUpstreamError(
                status_code=response.status_code,
                body=response.content,
                media_type=response.headers.get("content-type", "application/json"),
            )
            # Single forced refresh on 401 when using the OAuth store (not env bypass).
            if (
                response.status_code == 401
                and attempt == 0
                and not self._settings.access_token
            ):
                last_error = error
                continue
            raise error

        assert last_error is not None
        raise last_error

    async def open_stream(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[httpx.AsyncClient, httpx.Response]:
        """Open a streaming upstream response. Caller owns client lifecycle."""

        last_error: GrokUpstreamError | None = None
        for attempt in range(2):
            force_refresh = attempt > 0
            try:
                access_token = await self._tokens.acquire(force_refresh=force_refresh)
            except (GrokAuthError, GrokTokenUnavailableError):
                if last_error is not None:
                    raise last_error from None
                raise

            client = httpx.AsyncClient(timeout=self._settings.request_timeout)
            try:
                request = client.build_request(
                    method,
                    self._url(path),
                    headers=self._headers(access_token, extra_headers=extra_headers),
                    json=json_body,
                    params=params,
                )
                response = await client.send(request, stream=True)
            except Exception:
                await client.aclose()
                raise

            if response.status_code < 400:
                return client, response

            body = await response.aread()
            media_type = response.headers.get("content-type", "application/json")
            await response.aclose()
            await client.aclose()
            error = GrokUpstreamError(
                status_code=response.status_code,
                body=body,
                media_type=media_type,
            )
            if (
                response.status_code == 401
                and attempt == 0
                and not self._settings.access_token
            ):
                last_error = error
                continue
            raise error

        assert last_error is not None
        raise last_error

    def _url(self, path: str) -> str:
        base = self._settings.xai_base_url.rstrip("/")
        suffix = path if path.startswith("/") else f"/{path}"
        # base already includes /v1; callers pass paths relative to that root.
        if suffix.startswith("/v1/"):
            return f"{base}{suffix[3:]}" if base.endswith("/v1") else f"{base}{suffix}"
        return f"{base}{suffix}"

    def _headers(
        self,
        access_token: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": self._settings.user_agent,
            "Accept": "application/json",
        }
        if extra_headers:
            for name, value in extra_headers.items():
                if name.lower() in _FORWARD_HEADER_NAMES and value:
                    # Preserve canonical lowercase form used by xAI docs.
                    headers[name.lower()] = value
        return headers
