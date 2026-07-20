"""Upstream ChatGPT Codex transport for the native project proxy."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
import json
import secrets
from typing import Any, Callable, Literal
import urllib.error
import urllib.request
from urllib.parse import urlparse

import anyio
import anyio.to_thread
import httpx
from pydantic import BaseModel, ConfigDict

from lincy.llm.schema import (
    ContentPart,
    CodexCompactRequest,
    CodexCompactResponse,
    CodexNativeRequest,
    LLMResponse,
    Message,
    ToolCall,
    ToolDefinition,
    make_tool_result_message,
)

from .auth import (
    CodexAuthLoader,
    CodexBrowserAuthorization,
    CodexOAuthClient,
    StoredCodexToken,
    StoredCodexTokenStore,
    extract_chatgpt_account_id,
    extract_token_expiry,
    is_token_fresh,
    parse_callback_query,
    parse_manual_callback_value,
    render_callback_html,
)
from .settings import CodexProxySettings

# Usage snapshots hit one upstream endpoint per pool entry; cache aligned with
# the dashboard's polling so each viewer triggers at most one sweep.
USAGE_CACHE_TTL_SECONDS = 60.0

# Reverse-engineered endpoint; see docs/dev/provider-api-spec.md.
CODEX_USAGE_PATH = "/codex/usage"

# Upstream status codes that should trigger failover to the next token rather than
# surfacing the error to the client. Codex quota exhaustion may surface as 429,
# while hard auth / entitlement failures commonly surface as 401/403.
FAILOVER_STATUS_CODES = frozenset({401, 403, 429})

# How long a token stays benched after an upstream auth failure. A cooldown (rather
# than a permanent bench) lets a token that failed on a transient 401/403 rejoin the
# failover pool automatically, so a single blip cannot shrink the pool until restart.
FAILURE_COOLDOWN_SECONDS = 300.0

# A pending web login holds PKCE state in memory; abandoned flows expire so the
# dict cannot grow unbounded.
LOGIN_STATE_TTL_SECONDS = 900.0
# Completed logins stay around briefly so a polling client can observe the
# transition from pending to completed before the entry is pruned.
COMPLETED_LOGIN_TTL_SECONDS = 300.0


class CodexUpstreamError(RuntimeError):
    """Wrap upstream HTTP errors while preserving raw response payload."""

    def __init__(self, *, status_code: int, body: str, media_type: str = "application/json"):
        super().__init__(body)
        self.status_code = status_code
        self.body = body
        self.media_type = media_type


class CodexUpstreamTimeoutError(RuntimeError):
    """All usable tokens timed out while waiting for upstream response headers."""


class CodexTokenUnavailableError(RuntimeError):
    """No usable Codex OAuth token is available to serve the request."""


def _now() -> float:
    return datetime.now(tz=UTC).timestamp()


class _TolerantModel(BaseModel):
    """Parse reverse-engineered payloads without breaking on unknown fields."""

    model_config = ConfigDict(extra="ignore")


class _CodexUsageWindow(_TolerantModel):
    used_percent: float | None = None
    limit_window_seconds: int | None = None
    reset_at: int | float | None = None


class _CodexRateLimit(_TolerantModel):
    primary_window: _CodexUsageWindow | None = None
    secondary_window: _CodexUsageWindow | None = None


class CodexUsagePayload(_TolerantModel):
    email: str | None = None
    plan_type: str | None = None
    rate_limit: _CodexRateLimit | None = None


@dataclass(frozen=True)
class PoolEntry:
    """One credential in the failover pool, resolved for usage reporting."""

    token_id: str
    source: str
    access_token: str | None
    account_id: str
    benched: bool
    error: str | None


@dataclass
class _TurnStateEntry:
    value: str
    updated_at: datetime


class _CodexTurnStateStore:
    """Persist x-codex-turn-state per local turn for sticky routing."""

    _MAX_ENTRIES = 512

    def __init__(self) -> None:
        self._states: dict[str, _TurnStateEntry] = {}
        self._lock = anyio.Lock()

    async def get(self, turn_id: str | None) -> str | None:
        if not turn_id:
            return None
        async with self._lock:
            entry = self._states.get(turn_id)
            if entry is None:
                return None
            entry.updated_at = datetime.now(tz=UTC)
            return entry.value

    async def remember(self, turn_id: str | None, value: str | None) -> None:
        if not turn_id or not value:
            return
        async with self._lock:
            self._states[turn_id] = _TurnStateEntry(
                value=value,
                updated_at=datetime.now(tz=UTC),
            )
            self._prune_locked()

    def _prune_locked(self) -> None:
        if len(self._states) <= self._MAX_ENTRIES:
            return
        oldest_turn_id = min(
            self._states,
            key=lambda turn_id: self._states[turn_id].updated_at,
        )
        self._states.pop(oldest_turn_id, None)


class CodexTokenManager:
    """Load, cache, and refresh Codex OAuth tokens (multi-token failover pool)."""

    def __init__(self, settings: CodexProxySettings):
        self._settings = settings
        self._codex_auth = CodexAuthLoader(path=settings.codex_auth_path)
        self._store = StoredCodexTokenStore(settings.token_path)
        self._lock = anyio.Lock()
        # token id -> epoch seconds until which the token stays benched.
        self._failed_until: dict[str, float] = {}
        # In-memory-only refresh of the official auth-file token; never written
        # back to ~/.codex/auth.json (that file stays owned by the official CLI).
        self._official_refreshed: StoredCodexToken | None = None

    async def acquire(self) -> tuple[StoredCodexToken, str]:
        """Return (token, token_id) for the highest-priority usable token.

        The full token is returned (not just the access token string) because
        upstream request headers also need chatgpt-account-id.
        """

        async with self._lock:
            return await self._select_usable_token()

    def mark_failed(self, token_id: str) -> None:
        """Bench a token for FAILURE_COOLDOWN_SECONDS so acquire() skips it meanwhile."""

        self._failed_until[token_id] = _now() + FAILURE_COOLDOWN_SECONDS

    def promote(self, token_id: str) -> bool:
        return self._store.promote(token_id)

    def remove(self, token_id: str) -> bool:
        removed = self._store.remove(token_id)
        if removed:
            self._failed_until.pop(token_id, None)
        return removed

    def store_token(self, token: StoredCodexToken) -> None:
        self._store.save(token)

    async def pool_entries(self) -> list[PoolEntry]:
        """Snapshot every credential in priority order for usage reporting.

        Unlike acquire(), benched tokens are included (their utilization is
        usually the reason they are benched) and stale tokens are refreshed so
        the usage endpoint can still be queried.
        """

        entries: list[PoolEntry] = []
        async with self._lock:
            candidates, _ = self._load_candidates()
            for token, allow_writeback in candidates:
                benched = self._is_benched(token.id)
                if is_token_fresh(token):
                    entries.append(
                        PoolEntry(
                            token.id, token.source, token.access_token, token.account_id, benched, None
                        )
                    )
                    continue
                if token.refresh_token:
                    try:
                        refreshed = await self._refresh(token, writeback=allow_writeback)
                        entries.append(
                            PoolEntry(
                                token.id,
                                refreshed.source,
                                refreshed.access_token,
                                refreshed.account_id,
                                benched,
                                None,
                            )
                        )
                    except Exception as exc:
                        entries.append(
                            PoolEntry(
                                token.id, token.source, None, token.account_id, benched,
                                f"refresh failed: {exc}",
                            )
                        )
                    continue
                entries.append(
                    PoolEntry(
                        token.id,
                        token.source,
                        None,
                        token.account_id,
                        benched,
                        "token expired and has no refresh token",
                    )
                )
        return entries

    def _is_benched(self, token_id: str) -> bool:
        return self._failed_until.get(token_id, 0.0) > _now()

    def _load_candidates(self) -> tuple[list[tuple[StoredCodexToken, bool]], str | None]:
        """Pool members in priority order: (token, allow_writeback_on_refresh).

        Store tokens (newest created_at first) take priority; the official CLI
        auth file is an implicit lowest-priority fallback, skipped when a store
        token already covers the same ChatGPT account (avoids duplicate pool
        entries for one account).
        """

        store_tokens = self._store.load_all()
        candidates: list[tuple[StoredCodexToken, bool]] = [(t, True) for t in store_tokens]
        store_account_ids = {t.account_id for t in store_tokens}

        official, official_error = self._load_official_fallback()
        if official is not None and official.account_id not in store_account_ids:
            candidates.append((official, False))
        return candidates, official_error

    def _load_official_fallback(self) -> tuple[StoredCodexToken | None, str | None]:
        """Return the official CLI auth-file pool entry, refreshed in memory.

        Prefers a fresh on-disk read so a fresh `codex login` is picked up
        immediately; otherwise reuses our last in-memory refresh rather than
        hitting the refresh endpoint on every call.
        """

        try:
            loaded = self._codex_auth.load()
        except ValueError as exc:
            return None, str(exc)
        if loaded is not None and is_token_fresh(loaded):
            self._official_refreshed = None
            return loaded, None
        if self._official_refreshed is not None and is_token_fresh(self._official_refreshed):
            return self._official_refreshed, None
        return loaded, None

    async def _select_usable_token(self) -> tuple[StoredCodexToken, str]:
        candidates, load_error = self._load_candidates()
        errors: list[str] = [load_error] if load_error else []

        for token, allow_writeback in candidates:
            if self._is_benched(token.id):
                continue
            if is_token_fresh(token):
                return token, token.id
            if token.refresh_token:
                try:
                    refreshed = await self._refresh(token, writeback=allow_writeback)
                    return refreshed, refreshed.id
                except Exception as exc:
                    errors.append(f"refresh failed for {token.id}: {exc}")
                    continue
            # Stale and no refresh token: bench it so we don't keep re-selecting it.
            self.mark_failed(token.id)

        if not candidates:
            raise CodexTokenUnavailableError(
                "No Codex OAuth tokens available. Run `uv run proxy codex login`, or run "
                f"the official `codex login` so {self._settings.codex_auth_path} exists."
            )
        detail = "; ".join(errors) if errors else "all available tokens are expired/failed"
        raise CodexTokenUnavailableError(
            f"Codex token is required. Run `uv run proxy codex login`. ({detail})"
        )

    async def _refresh(self, token: StoredCodexToken, *, writeback: bool) -> StoredCodexToken:
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "client_id": self._settings.oauth_client_id,
        }
        async with httpx.AsyncClient(timeout=self._settings.request_timeout) as client:
            response = await client.post(
                self._settings.oauth_token_url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"OAuth refresh failed with status {response.status_code}: {response.text}"
            )
        data = response.json()
        access_token = data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise RuntimeError("OAuth refresh returned no access_token")
        next_refresh_token = data.get("refresh_token")
        if next_refresh_token is not None and not isinstance(next_refresh_token, str):
            raise RuntimeError("OAuth refresh returned invalid refresh_token")
        refreshed = StoredCodexToken(
            id=token.id,
            access_token=access_token,
            refresh_token=next_refresh_token or token.refresh_token,
            account_id=extract_chatgpt_account_id(access_token),
            expires_at=extract_token_expiry(access_token),
            source="oauth_refresh",
            client_id=self._settings.oauth_client_id,
            created_at=token.created_at,
        )
        if writeback:
            self._store.save(refreshed)
        else:
            # Official auth-file token: keep the refresh in memory only, never
            # rewrite ~/.codex/auth.json (that file stays owned by `codex login`).
            self._official_refreshed = refreshed
        return refreshed


@dataclass
class _PendingLogin:
    authorization: CodexBrowserAuthorization
    expires_at: float
    status: Literal["pending", "completed"] = "pending"
    token_id: str | None = None


_HTTP_REASONS = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    500: "Internal Server Error",
    502: "Bad Gateway",
}


class _CodexCallbackListener:
    """Background asyncio TCP listener for the codex browser OAuth callback.

    Runs only while a login is pending so serve does not squat on the
    callback port (which the official `codex login` may also want to bind)
    between logins. Started on demand by begin_login(). The request-parsing
    and HTML-response helpers are shared with the blocking CLI listener in
    codex_proxy.auth (wait_for_browser_callback).
    """

    def __init__(
        self,
        on_callback: Callable[[str, str], Any],
        *,
        host: str,
        port: int,
        path: str,
    ):
        self._on_callback = on_callback
        self._host = host
        self._port = port
        self._path = path
        self._server: asyncio.Server | None = None

    @property
    def bound_port(self) -> int | None:
        if self._server is None or not self._server.sockets:
            return None
        return self._server.sockets[0].getsockname()[1]

    async def ensure_started(self) -> str | None:
        """Start the listener if not already running. Return an error string on bind failure."""

        if self._server is not None:
            return None
        try:
            self._server = await asyncio.start_server(self._handle_connection, self._host, self._port)
        except OSError as exc:
            return str(exc)
        return None

    async def stop(self) -> None:
        """Stop accepting new connections.

        Deliberately does not await server.wait_closed(): the caller (a
        finished login) runs from inside the very connection handler that is
        being served, and wait_closed() blocks until in-flight connections
        finish -- which would deadlock against this one. close() alone stops
        new accepts immediately; the in-flight response still gets written
        and its connection still closes normally right after this returns.
        """

        if self._server is None:
            return
        server = self._server
        self._server = None
        server.close()

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request_line = await reader.readline()
            while True:
                line = await reader.readline()
                if not line or line in (b"\r\n", b"\n"):
                    break
            status, body = await self._respond_to(request_line)
        except Exception:
            status, body = 500, render_callback_html(success=False, message="Internal error.")
        try:
            await self._write_response(writer, status, body)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _respond_to(self, request_line: bytes) -> tuple[int, str]:
        try:
            method, target, _ = request_line.decode("ascii", errors="replace").split(" ", 2)
        except ValueError:
            return 400, render_callback_html(success=False, message="Malformed request.")
        parsed = urlparse(target)
        if method != "GET" or parsed.path != self._path:
            return 404, render_callback_html(success=False, message="Not found.")
        code, state, error = parse_callback_query(parsed.query)
        if error:
            return 400, render_callback_html(success=False, message=error)
        if not code or not state:
            return 400, render_callback_html(success=False, message="Missing code or state parameter.")
        try:
            await self._on_callback(code, state)
        except ValueError as exc:
            return 404, render_callback_html(success=False, message=str(exc))
        except RuntimeError as exc:
            return 502, render_callback_html(success=False, message=str(exc))
        return 200, render_callback_html(success=True, message=None)

    @staticmethod
    async def _write_response(writer: asyncio.StreamWriter, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        reason = _HTTP_REASONS.get(status, "OK")
        header = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(encoded)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        writer.write(header + encoded)
        await writer.drain()


class CodexProxyService:
    """Translate native proxy requests into ChatGPT Codex backend calls."""

    def __init__(self, settings: CodexProxySettings):
        self._settings = settings
        self._tokens = CodexTokenManager(settings)
        self._turn_states = _CodexTurnStateStore()
        self._usage_cache: tuple[float, dict[str, Any]] | None = None
        self._usage_lock = anyio.Lock()
        # token id -> last successful (account, usage), served with stale=True
        # when a later fetch fails.
        self._last_good_usage: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self._oauth = CodexOAuthClient(
            request_timeout=settings.request_timeout,
            client_id=settings.oauth_client_id,
            authorize_url=settings.oauth_authorize_url,
            token_url=settings.oauth_token_url,
            redirect_uri=settings.oauth_redirect_uri,
            scope=settings.oauth_scope,
        )
        # login id -> pending/completed state for the web login flow.
        self._pending_logins: dict[str, _PendingLogin] = {}
        callback_path = urlparse(settings.oauth_redirect_uri).path or "/auth/callback"
        self._callback_listener = _CodexCallbackListener(
            self._complete_pending_by_state,
            host=settings.callback_bind_host,
            port=settings.callback_bind_port,
            path=callback_path,
        )

    @property
    def callback_listener_port(self) -> int | None:
        """Bound port of the background OAuth callback listener, if running.

        Exposed for tests that bind port 0 and need the OS-assigned port.
        """

        return self._callback_listener.bound_port

    def invalidate_usage_cache(self) -> None:
        """Drop the usage snapshot so the next read reflects a token-store edit."""

        self._usage_cache = None

    def promote_token(self, token_id: str) -> bool:
        promoted = self._tokens.promote(token_id)
        if promoted:
            self.invalidate_usage_cache()
        return promoted

    def remove_token(self, token_id: str) -> bool:
        removed = self._tokens.remove(token_id)
        if removed:
            self.invalidate_usage_cache()
        return removed

    async def begin_login(self) -> dict[str, Any]:
        """Start a browser OAuth flow for web-driven account adds.

        Ensures the local callback listener is running so a successful browser
        redirect completes the login without a manual paste. Bind failures
        (e.g. the official `codex login` already holding the port) are
        reported alongside the URL so the manual-paste fallback still works.
        """

        self._prune_pending_logins()
        authorization = self._oauth.begin_authorization()
        login_id = secrets.token_urlsafe(8)
        self._pending_logins[login_id] = _PendingLogin(
            authorization=authorization,
            expires_at=_now() + LOGIN_STATE_TTL_SECONDS,
        )
        result: dict[str, Any] = {
            "login_id": login_id,
            "authorization_url": authorization.authorization_url,
        }
        listener_error = await self._callback_listener.ensure_started()
        if listener_error:
            result["listener_error"] = listener_error
        return result

    def login_status(self, login_id: str) -> dict[str, Any]:
        self._prune_pending_logins()
        entry = self._pending_logins.get(login_id)
        if entry is None:
            return {"status": "expired", "token_id": None}
        return {"status": entry.status, "token_id": entry.token_id}

    async def complete_login(self, login_id: str, value: str) -> StoredCodexToken | None:
        """Manual fallback: exchange a pasted callback URL / `code#state`.

        Returns None when the login id is unknown, expired, or already
        completed (the browser callback may have already finished it). State
        mismatch and exchange failures propagate as ValueError / RuntimeError.
        """

        self._prune_pending_logins()
        entry = self._pending_logins.get(login_id)
        if entry is None or entry.status != "pending":
            return None
        code, state = parse_manual_callback_value(value)
        return await self._finish_login(entry, code, state)

    async def _complete_pending_by_state(self, code: str, state: str) -> None:
        """Match an inbound callback GET to a pending login and finish it."""

        self._prune_pending_logins()
        entry = next(
            (
                entry
                for entry in self._pending_logins.values()
                if entry.status == "pending" and entry.authorization.state == state
            ),
            None,
        )
        if entry is None:
            raise ValueError("Unknown or expired login state. Restart the login flow.")
        await self._finish_login(entry, code, state)

    async def _finish_login(
        self, entry: _PendingLogin, code: str, state: str
    ) -> StoredCodexToken:
        if state != entry.authorization.state:
            raise ValueError("Authorization state mismatch. Restart the login flow.")
        # The OAuth client is sync httpx; keep the event loop free for other requests.
        token = await anyio.to_thread.run_sync(
            partial(
                self._oauth.exchange_callback_code,
                code,
                returned_state=state,
                authorization=entry.authorization,
            )
        )
        self._tokens.store_token(token)
        entry.status = "completed"
        entry.token_id = token.id
        entry.expires_at = _now() + COMPLETED_LOGIN_TTL_SECONDS
        self.invalidate_usage_cache()
        if not self._has_pending_logins():
            await self._callback_listener.stop()
        return token

    def _has_pending_logins(self) -> bool:
        return any(entry.status == "pending" for entry in self._pending_logins.values())

    def _prune_pending_logins(self) -> None:
        now = _now()
        expired = [key for key, entry in self._pending_logins.items() if entry.expires_at <= now]
        for key in expired:
            del self._pending_logins[key]

    async def usage_snapshot(self, force_refresh: bool = False) -> dict[str, Any]:
        """Report account identity and rate-limit windows per pool entry.

        One failing account must not break the snapshot: fetch errors are
        reported per entry. The whole snapshot is cached briefly (the lock
        also collapses concurrent dashboard refreshes into one upstream
        sweep); force_refresh bypasses the cache read for a manual refresh,
        but still stores the fresh result.
        """

        async with self._usage_lock:
            if self._usage_cache is not None and not force_refresh:
                fetched_at, cached = self._usage_cache
                if _now() - fetched_at < USAGE_CACHE_TTL_SECONDS:
                    return cached

            entries = await self._tokens.pool_entries()
            accounts: list[dict[str, Any]] = []
            active_seen = False
            for priority, entry in enumerate(entries):
                item: dict[str, Any] = {
                    "id": entry.token_id,
                    "source": entry.source,
                    "priority": priority,
                    "status": "unusable",
                    "error": entry.error,
                    "account": None,
                    "usage": None,
                    "stale": False,
                }
                if entry.access_token is not None:
                    if entry.benched:
                        item["status"] = "benched"
                    elif not active_seen:
                        item["status"] = "active"
                        active_seen = True
                    else:
                        item["status"] = "standby"
                    try:
                        account, usage = await self._fetch_account_usage(
                            access_token=entry.access_token,
                            account_id=entry.account_id,
                        )
                        item["account"] = account
                        item["usage"] = usage
                        self._last_good_usage[entry.token_id] = (account, usage)
                    except Exception as exc:
                        item["error"] = f"usage fetch failed: {exc}"
                        last_good = self._last_good_usage.get(entry.token_id)
                        if last_good is not None:
                            item["account"], item["usage"] = last_good
                            item["stale"] = True
                accounts.append(item)

            # Codex has no models passthrough; keep the key for shape parity
            # with the other proxies' /usage snapshots.
            payload = {"accounts": accounts, "models": []}
            self._usage_cache = (_now(), payload)
            return payload

    def _base_headers(self, *, access_token: str, account_id: str) -> dict[str, str]:
        # Reverse-engineered from:
        # https://github.com/insightflo/chatgpt-codex-proxy (src/codex/client.ts)
        # https://github.com/icebear0828/codex-proxy
        return {
            "Authorization": f"Bearer {access_token}",
            "chatgpt-account-id": account_id,
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
            # Default python client UAs get a 403 HTML edge challenge on
            # /codex/usage; the official CLI UA passes (verified 2026-07-18).
            "User-Agent": self._settings.user_agent,
        }

    def _usage_headers(self, *, access_token: str, account_id: str) -> dict[str, str]:
        return {
            **self._base_headers(access_token=access_token, account_id=account_id),
            "Accept": "application/json",
        }

    async def _fetch_account_usage(
        self,
        *,
        access_token: str,
        account_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        headers = self._usage_headers(access_token=access_token, account_id=account_id)
        status, text = await anyio.to_thread.run_sync(
            _sync_usage_get,
            f"{self._settings.codex_base_url}{CODEX_USAGE_PATH}",
            headers,
            self._settings.request_timeout,
        )
        if status >= 400:
            raise RuntimeError(f"HTTP {status}: {text[:200]}")
        payload = CodexUsagePayload.model_validate(json.loads(text))
        account = {"email": payload.email, "plan_type": payload.plan_type}
        usage = {"windows": _usage_windows(payload.rate_limit)}
        return account, usage

    async def chat(self, request: CodexNativeRequest) -> LLMResponse:
        payload = self._build_upstream_request(request)
        turn_state = await self._turn_states.get(request.turn_id)
        last_upstream: CodexUpstreamError | None = None
        last_timeout: CodexUpstreamTimeoutError | None = None
        while True:
            try:
                token, token_id = await self._tokens.acquire()
            except CodexTokenUnavailableError:
                # Every token was benched on upstream failures: surface the real
                # upstream error rather than an opaque "no token available".
                if last_upstream is not None:
                    raise last_upstream from None
                if last_timeout is not None:
                    raise last_timeout from None
                raise
            try:
                async with httpx.AsyncClient(timeout=self._settings.request_timeout) as client:
                    response = await client.post(
                        f"{self._settings.codex_base_url}/codex/responses",
                        headers=self._headers(
                            token,
                            session_id=request.session_id,
                            turn_id=request.turn_id,
                            turn_state=turn_state,
                        ),
                        json=payload,
                    )
            except httpx.ReadTimeout:
                self._tokens.mark_failed(token_id)
                last_timeout = CodexUpstreamTimeoutError(
                    f"Codex upstream timed out for token {token_id}"
                )
                continue
            await self._turn_states.remember(
                request.turn_id,
                response.headers.get("x-codex-turn-state"),
            )
            if response.status_code < 400:
                return _parse_sse_response(response.text)
            error = CodexUpstreamError(
                status_code=response.status_code,
                body=response.text,
                media_type=response.headers.get("content-type", "application/json"),
            )
            if response.status_code in FAILOVER_STATUS_CODES:
                self._tokens.mark_failed(token_id)
                last_upstream = error
                continue
            raise error

    async def compact(self, request: CodexCompactRequest) -> CodexCompactResponse:
        payload = self._build_upstream_compaction_request(request)
        last_upstream: CodexUpstreamError | None = None
        last_timeout: CodexUpstreamTimeoutError | None = None
        while True:
            try:
                token, token_id = await self._tokens.acquire()
            except CodexTokenUnavailableError:
                if last_upstream is not None:
                    raise last_upstream from None
                if last_timeout is not None:
                    raise last_timeout from None
                raise
            try:
                async with httpx.AsyncClient(timeout=self._settings.request_timeout) as client:
                    response = await client.post(
                        f"{self._settings.codex_base_url}/codex/responses/compact",
                        headers=self._headers(
                            token,
                            session_id=request.session_id,
                            turn_id=request.turn_id,
                            turn_state=None,
                        ),
                        json=payload,
                    )
            except httpx.ReadTimeout:
                self._tokens.mark_failed(token_id)
                last_timeout = CodexUpstreamTimeoutError(
                    f"Codex upstream timed out for token {token_id}"
                )
                continue
            if response.status_code < 400:
                data = response.json()
                output = data.get("output")
                if not isinstance(output, list):
                    raise ValueError("Codex compact response missing output list")
                return CodexCompactResponse(messages=_parse_compaction_output_items(output))
            error = CodexUpstreamError(
                status_code=response.status_code,
                body=response.text,
                media_type=response.headers.get("content-type", "application/json"),
            )
            if response.status_code in FAILOVER_STATUS_CODES:
                self._tokens.mark_failed(token_id)
                last_upstream = error
                continue
            raise error

    def _build_upstream_request(self, request: CodexNativeRequest) -> dict[str, Any]:
        upstream: dict[str, Any] = {
            "model": request.model,
            "instructions": _extract_system_instructions(request.messages),
            "input": _convert_messages(request.messages),
            "stream": True,
            "store": False,
        }
        if not upstream["input"]:
            upstream["input"] = [{"type": "message", "role": "user", "content": ""}]
        # The ChatGPT Codex backend currently rejects top-level max_output_tokens
        # with HTTP 400 for all verified OAuth models. Keep the native field for
        # local compatibility, but do not forward it upstream.
        if request.reasoning_effort is not None:
            upstream["reasoning"] = {
                "effort": request.reasoning_effort,
                "summary": "auto",
            }
        if request.tools:
            upstream["tools"] = _convert_tools(request.tools)
            upstream["tool_choice"] = "auto"
        if request.response_schema is not None:
            upstream["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "response",
                    "schema": request.response_schema,
                    "strict": False,
                }
            }
        if request.temperature is not None:
            upstream["temperature"] = request.temperature
        if request.prompt_cache_key:
            # Reverse-engineered from:
            # https://github.com/icebear0828/codex-proxy (prompt_cache_key transport)
            upstream["prompt_cache_key"] = request.prompt_cache_key
        return upstream

    def _build_upstream_compaction_request(
        self,
        request: CodexCompactRequest,
    ) -> dict[str, Any]:
        upstream: dict[str, Any] = {
            "model": request.model,
            "instructions": _extract_system_instructions(request.messages),
            "input": _convert_messages(request.messages),
            "tools": _convert_tools(request.tools or []),
            "parallel_tool_calls": bool(request.tools),
        }
        if request.reasoning_effort is not None:
            upstream["reasoning"] = {
                "effort": request.reasoning_effort,
                "summary": "auto",
            }
        return upstream

    def _headers(
        self,
        token: StoredCodexToken,
        *,
        session_id: str | None,
        turn_id: str | None,
        turn_state: str | None,
    ) -> dict[str, str]:
        headers = {
            **self._base_headers(access_token=token.access_token, account_id=token.account_id),
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if session_id:
            # Official Codex CLI sends the conversation id as session_id.
            # See openai/codex codex-rs/codex-api/src/requests/headers.rs.
            headers["session_id"] = session_id
        if turn_state:
            # Official Codex CLI replays x-codex-turn-state within the same turn
            # so follow-up requests stay on the same backend shard.
            # See openai/codex codex-rs/core/tests/suite/turn_state.rs.
            headers["x-codex-turn-state"] = turn_state
        if turn_id:
            # Match the official CLI shape closely enough for backend observability.
            headers["x-codex-turn-metadata"] = json.dumps({"turn_id": turn_id})
        return headers


def _sync_usage_get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
    """Fetch the usage endpoint with stdlib urllib instead of httpx.

    chatgpt.com's edge bot rules reject httpx/httpcore requests on
    /codex/usage with a 403 HTML challenge regardless of headers
    (client-shape fingerprint), while stdlib urllib and urllib3 pass;
    /codex/responses is not affected. Verified empirically 2026-07-18.
    Runs in a worker thread via anyio.to_thread.run_sync.
    """

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _usage_windows(rate_limit: _CodexRateLimit | None) -> list[dict[str, Any]]:
    if rate_limit is None:
        return []
    windows: list[dict[str, Any]] = []
    for window in (rate_limit.primary_window, rate_limit.secondary_window):
        if window is None or window.used_percent is None:
            continue
        windows.append(
            {
                "label": _window_label(window.limit_window_seconds),
                "utilization": float(window.used_percent),
                "resets_at": _format_reset_at(window.reset_at),
            }
        )
    return windows


def _window_label(limit_window_seconds: int | None) -> str:
    if limit_window_seconds is None:
        return "unknown"
    if limit_window_seconds == 604800:
        return "Week"
    if limit_window_seconds % 3600 == 0:
        hours = limit_window_seconds // 3600
        if hours < 24:
            return f"{hours}h"
    return f"{limit_window_seconds // 86400}d"


def _format_reset_at(reset_at: int | float | None) -> str | None:
    if reset_at is None:
        return None
    return datetime.fromtimestamp(float(reset_at), tz=UTC).isoformat()


def _convert_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.to_json_schema(),
        }
        for tool in tools
    ]


def _repair_missing_tool_results(messages: list[Message]) -> list[Message]:
    repaired: list[Message] = []
    idx = 0
    while idx < len(messages):
        msg = messages[idx]
        repaired.append(msg)
        if msg.role != "assistant" or not msg.tool_calls:
            idx += 1
            continue

        expected = {tc.id: tc.name for tc in msg.tool_calls if tc.id}
        idx += 1
        while idx < len(messages) and messages[idx].role == "tool":
            tool_msg = messages[idx]
            repaired.append(tool_msg)
            if tool_msg.tool_call_id in expected:
                expected.pop(tool_msg.tool_call_id, None)
            idx += 1

        for missing_id, missing_name in expected.items():
            repaired.append(
                make_tool_result_message(
                    tool_call_id=missing_id,
                    name=missing_name,
                    content="[Recovered missing tool result]",
                )
            )
    return repaired


def _extract_system_instructions(messages: list[Message]) -> str:
    chunks: list[str] = []
    for message in messages:
        if message.role != "system":
            continue
        if isinstance(message.content, str) and message.content:
            chunks.append(message.content)
        elif isinstance(message.content, list):
            for part in message.content:
                if part.type == "text" and part.text:
                    chunks.append(part.text)
    return "\n\n".join(chunks)


def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
    repaired = _repair_missing_tool_results(messages)
    result: list[dict[str, Any]] = []
    pending_images: list[dict[str, Any]] = []

    for message in repaired:
        if message.codex_compaction_encrypted_content:
            if pending_images:
                result.append({"type": "message", "role": "user", "content": pending_images})
                pending_images = []
            result.append(
                {
                    "type": "compaction_summary",
                    "encrypted_content": message.codex_compaction_encrypted_content,
                }
            )
            continue

        if message.role == "system":
            continue

        if message.role != "tool" and pending_images:
            result.append({"type": "message", "role": "user", "content": pending_images})
            pending_images = []

        if message.role == "tool":
            result.append(_convert_tool_result(message, pending_images))
            continue

        if message.role == "assistant" and message.tool_calls:
            converted = _convert_regular_message(message)
            if converted is not None:
                result.append(converted)
            for tool_call in message.tool_calls:
                result.append(
                    {
                        "type": "function_call",
                        "call_id": tool_call.id,
                        "name": tool_call.name,
                        "arguments": json.dumps(tool_call.arguments),
                    }
                )
            continue

        converted = _convert_regular_message(message)
        if converted is not None:
            result.append(converted)

    if pending_images:
        result.append({"type": "message", "role": "user", "content": pending_images})

    return result


def _convert_tool_result(
    message: Message,
    pending_images: list[dict[str, Any]],
) -> dict[str, Any]:
    if isinstance(message.content, list):
        text_parts = [
            part.text
            for part in message.content
            if part.type == "text" and part.text
        ]
        pending_images.extend(_extract_image_parts(message.content))
        output = "\n".join(text_parts) if text_parts else ""
    else:
        output = message.content or ""
    return {
        "type": "function_call_output",
        "call_id": message.tool_call_id or "tool_call",
        "output": output,
    }


def _convert_regular_message(message: Message) -> dict[str, Any] | None:
    role = "assistant" if message.role == "assistant" else "user"
    if isinstance(message.content, str):
        if not message.content and message.role == "assistant" and message.tool_calls:
            return None
        return {
            "type": "message",
            "role": role,
            "content": message.content or "",
        }
    if not isinstance(message.content, list):
        return None

    text_type = "output_text" if role == "assistant" else "input_text"
    parts: list[dict[str, Any]] = []
    for part in message.content:
        if part.type == "text" and part.text:
            parts.append({"type": text_type, "text": part.text})
        elif part.type == "image" and part.data and part.media_type and role == "user":
            parts.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{part.media_type};base64,{part.data}",
                }
            )
    if not parts:
        return None
    text_only = all(part["type"] == text_type for part in parts)
    if text_only:
        return {
            "type": "message",
            "role": role,
            "content": "\n".join(part["text"] for part in parts),
        }
    return {
        "type": "message",
        "role": role,
        "content": parts,
    }


def _extract_image_parts(parts: list[ContentPart]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for part in parts:
        if part.type == "image" and part.data and part.media_type:
            result.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{part.media_type};base64,{part.data}",
                }
            )
    return result


def _parse_sse_response(raw: str) -> LLMResponse:
    output_items: list[dict[str, Any]] = []
    final_response: dict[str, Any] | None = None
    output_text_chunks: list[str] = []
    reasoning_chunks: list[str] = []

    for data in _iter_sse_data(raw):
        if data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except ValueError:
            continue
        event_type = event.get("type")
        if event_type == "response.output_text.delta" and isinstance(event.get("delta"), str):
            output_text_chunks.append(event["delta"])
            continue
        if event_type in {"response.reasoning_summary_text.delta", "response.reasoning.delta"}:
            delta = event.get("delta")
            if isinstance(delta, str):
                reasoning_chunks.append(delta)
            continue
        if event_type == "response.output_item.done" and isinstance(event.get("item"), dict):
            output_items.append(event["item"])
            continue
        if event_type in {"response.completed", "response.done"} and isinstance(event.get("response"), dict):
            final_response = event["response"]
            continue
        if event_type in {"response.failed", "error"}:
            raise RuntimeError(_format_event_error(event))

    if final_response is not None:
        response_output = final_response.get("output")
        if not output_items and isinstance(response_output, list):
            output_items = [item for item in response_output if isinstance(item, dict)]

    content, reasoning_content, reasoning_details, tool_calls = _parse_output_items(
        output_items,
        fallback_text="".join(output_text_chunks) or None,
        fallback_reasoning="".join(reasoning_chunks) or None,
    )
    prompt_tokens, completion_tokens, total_tokens, cache_read_tokens, usage_available = _parse_usage(
        final_response.get("usage") if isinstance(final_response, dict) else None
    )
    finish_reason = "tool_calls" if tool_calls else _resolve_finish_reason(final_response)

    return LLMResponse(
        content=content,
        reasoning_content=reasoning_content,
        reasoning_details=reasoning_details,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        usage_available=usage_available,
        cache_read_tokens=cache_read_tokens,
    )


def _iter_sse_data(raw: str) -> list[str]:
    events: list[str] = []
    normalized = raw.replace("\r\n", "\n")
    for block in normalized.split("\n\n"):
        lines = [line for line in block.split("\n") if line.startswith("data:")]
        if not lines:
            continue
        payload = "\n".join(line[5:].lstrip() for line in lines)
        if payload:
            events.append(payload)
    return events


def _parse_output_items(
    output_items: list[dict[str, Any]],
    *,
    fallback_text: str | None,
    fallback_reasoning: str | None,
) -> tuple[str | None, str | None, list[dict[str, Any]] | None, list[ToolCall]]:
    content = None
    reasoning_parts: list[str] = []
    seen_reasoning: set[str] = set()
    reasoning_details: list[dict[str, Any]] = []
    tool_calls: list[ToolCall] = []

    for item in output_items:
        item_type = item.get("type")
        if item_type == "message":
            message_text = _extract_message_text(item)
            if message_text and content is None:
                content = message_text
            continue
        if item_type == "function_call":
            raw_arguments = item.get("arguments")
            tool_calls.append(
                ToolCall(
                    id=_string_or(item.get("call_id"), item.get("id"), default="tool_call"),
                    name=_string_or(item.get("name"), default="tool"),
                    arguments=_parse_arguments(raw_arguments),
                    provider_roundtrip=item,
                )
            )
            continue

        reasoning_text = _extract_reasoning_text(item)
        if reasoning_text:
            cleaned = reasoning_text.strip()
            if cleaned and cleaned not in seen_reasoning:
                seen_reasoning.add(cleaned)
                reasoning_parts.append(cleaned)
        reasoning_details.append(item)

    if content is None and fallback_text:
        content = fallback_text
    if fallback_reasoning:
        cleaned = fallback_reasoning.strip()
        if cleaned and cleaned not in seen_reasoning:
            reasoning_parts.append(cleaned)
    return (
        content,
        "\n\n".join(reasoning_parts) if reasoning_parts else None,
        reasoning_details or None,
        tool_calls,
    )


def _extract_message_text(item: dict[str, Any]) -> str | None:
    content = item.get("content")
    if isinstance(content, str):
        return content or None
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        text = part.get("text")
        if part_type in {"output_text", "text", "input_text"} and isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts) if parts else None


def _extract_reasoning_text(item: dict[str, Any]) -> str | None:
    text = item.get("text")
    if isinstance(text, str) and str(item.get("type", "")).startswith("reasoning"):
        return text
    summary = item.get("summary")
    if isinstance(summary, list):
        texts = [
            entry.get("text")
            for entry in summary
            if isinstance(entry, dict) and isinstance(entry.get("text"), str)
        ]
        if texts:
            return "\n".join(texts)
    content = item.get("content")
    if isinstance(content, list):
        texts = [
            part.get("text")
            for part in content
            if isinstance(part, dict)
            and isinstance(part.get("text"), str)
            and str(part.get("type", "")).startswith(("summary", "reasoning"))
        ]
        if texts:
            return "\n".join(texts)
    return None


def _parse_compaction_output_items(output_items: list[Any]) -> list[Message]:
    messages: list[Message] = []
    for item in output_items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in {"compaction", "compaction_summary"}:
            encrypted = item.get("encrypted_content")
            if isinstance(encrypted, str) and encrypted:
                messages.append(
                    Message(
                        role="assistant",
                        content="[Codex compaction checkpoint]",
                        codex_compaction_encrypted_content=encrypted,
                    )
                )
            continue
        if item_type != "message":
            continue
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = _extract_message_text(item)
        if not text:
            continue
        messages.append(Message(role=role, content=text))
    return messages


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw_arguments": raw}
    return parsed if isinstance(parsed, dict) else {"_raw_arguments": raw}


def _parse_usage(
    usage: Any,
) -> tuple[int | None, int | None, int | None, int, bool]:
    if not isinstance(usage, dict):
        return None, None, None, 0, False
    prompt_tokens = _int_or_none(usage.get("input_tokens"))
    completion_tokens = _int_or_none(usage.get("output_tokens"))
    total_tokens = _int_or_none(usage.get("total_tokens"))
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens
    input_details = usage.get("input_tokens_details")
    cache_read = 0
    if isinstance(input_details, dict):
        cache_read = _int_or_none(input_details.get("cached_tokens")) or 0
    return prompt_tokens, completion_tokens, total_tokens, cache_read, True


def _resolve_finish_reason(final_response: dict[str, Any] | None) -> str | None:
    if not isinstance(final_response, dict):
        return "stop"
    stop_reason = final_response.get("stop_reason")
    if isinstance(stop_reason, str) and stop_reason:
        return stop_reason
    status = final_response.get("status")
    if isinstance(status, str) and status:
        return "stop" if status == "completed" else status
    return "stop"


def _format_event_error(event: dict[str, Any]) -> str:
    error = event.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    message = event.get("message")
    if isinstance(message, str) and message:
        return message
    return json.dumps(event)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _string_or(*values: Any, default: str) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return default
