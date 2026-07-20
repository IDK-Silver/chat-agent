"""FastAPI app for the native Codex proxy."""

from __future__ import annotations

import ipaddress
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from lincy.llm.schema import CodexCompactRequest, CodexNativeRequest

from .auth import CODEX_AUTH_FALLBACK_TOKEN_ID, normalize_bearer_token
from .service import CodexProxyService, CodexTokenUnavailableError, CodexUpstreamError, CodexUpstreamTimeoutError
from .settings import CodexProxySettings


def _is_loopback_client(request: Request) -> bool:
    """True when the TCP peer is a loopback address (127.0.0.0/8 or ::1).

    Uses the socket peer address only; forwarding headers are untrusted. Peers
    that cannot be parsed as an IP are treated as remote (fail closed).
    """

    if request.client is None:
        return False
    try:
        address = ipaddress.ip_address(request.client.host)
    except ValueError:
        return False
    # A dual-stack bind reports local IPv4 peers as ::ffff:127.0.0.1.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def _reject_unauthorized(request: Request, api_key: str | None) -> JSONResponse | None:
    """Gate non-loopback clients behind the inbound API key.

    Loopback requests always pass. Without a configured key, remote requests
    are always rejected so binding a public host can never silently expose
    upstream quota.
    """

    if _is_loopback_client(request):
        return None
    if not api_key:
        return JSONResponse(
            {
                "error": (
                    "This proxy only accepts localhost requests. Set "
                    "CODEX_PROXY_API_KEY (or serve --api-key) to allow remote clients."
                )
            },
            status_code=401,
        )
    provided = request.headers.get("x-api-key") or normalize_bearer_token(
        request.headers.get("authorization", "")
    )
    if provided and secrets.compare_digest(provided, api_key):
        return None
    return JSONResponse(
        {
            "error": (
                "Invalid or missing API key. Provide it via x-api-key or "
                "Authorization: Bearer."
            )
        },
        status_code=401,
    )


class LoginCompleteRequest(BaseModel):
    """Pasted callback URL or `code#state` from the Codex callback page."""

    value: str = Field(min_length=1)


def create_app(settings: CodexProxySettings) -> FastAPI:
    app = FastAPI(title="chat-agent-codex-proxy", docs_url=None, redoc_url=None)
    service = CodexProxyService(settings)
    # Exposed for tests that need the ephemeral callback-listener port bound
    # under callback_bind_port=0; not used by any route handler.
    app.state.service = service

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # /chat and /compact predate the inbound gate and the local provider client
    # (lincy.llm.providers.codex) sends no key; leave them ungated like
    # today. The management surface below (usage/login/tokens) is new and gated.

    @app.post("/chat")
    async def chat(request: CodexNativeRequest):
        try:
            response = await service.chat(request)
        except CodexUpstreamError as exc:
            return Response(
                content=exc.body,
                status_code=exc.status_code,
                media_type=exc.media_type,
            )
        except CodexTokenUnavailableError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        except CodexUpstreamTimeoutError as exc:
            return JSONResponse({"error": str(exc)}, status_code=504)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)
        return JSONResponse(response.model_dump())

    @app.post("/compact")
    async def compact(request: CodexCompactRequest):
        try:
            response = await service.compact(request)
        except CodexUpstreamError as exc:
            return Response(
                content=exc.body,
                status_code=exc.status_code,
                media_type=exc.media_type,
            )
        except CodexTokenUnavailableError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        except CodexUpstreamTimeoutError as exc:
            return JSONResponse({"error": str(exc)}, status_code=504)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)
        return JSONResponse(response.model_dump())

    # Management surface for the web dashboard / CLI. Same inbound gate as
    # claude-code-proxy: loopback is trusted, remote needs the inbound API key.

    @app.get("/usage")
    async def usage(raw_request: Request, refresh: bool = False):
        rejection = _reject_unauthorized(raw_request, settings.api_key)
        if rejection is not None:
            return rejection
        return await service.usage_snapshot(force_refresh=refresh)

    @app.post("/tokens/{token_id}/promote")
    async def promote_token(token_id: str, raw_request: Request):
        rejection = _reject_unauthorized(raw_request, settings.api_key)
        if rejection is not None:
            return rejection
        if token_id == CODEX_AUTH_FALLBACK_TOKEN_ID:
            return JSONResponse(
                {"error": "the official codex CLI auth is managed by `codex login`, not this proxy"},
                status_code=404,
            )
        if not service.promote_token(token_id):
            return JSONResponse({"error": f"no token with id {token_id}"}, status_code=404)
        return {"ok": True}

    @app.delete("/tokens/{token_id}")
    async def remove_token(token_id: str, raw_request: Request):
        rejection = _reject_unauthorized(raw_request, settings.api_key)
        if rejection is not None:
            return rejection
        if token_id == CODEX_AUTH_FALLBACK_TOKEN_ID:
            return JSONResponse(
                {"error": "the official codex CLI auth is managed by `codex login`, not this proxy"},
                status_code=404,
            )
        if not service.remove_token(token_id):
            return JSONResponse({"error": f"no token with id {token_id}"}, status_code=404)
        return {"ok": True}

    @app.post("/login")
    async def begin_login(raw_request: Request):
        rejection = _reject_unauthorized(raw_request, settings.api_key)
        if rejection is not None:
            return rejection
        return await service.begin_login()

    @app.get("/login/{login_id}")
    async def login_status(login_id: str, raw_request: Request):
        rejection = _reject_unauthorized(raw_request, settings.api_key)
        if rejection is not None:
            return rejection
        return service.login_status(login_id)

    @app.post("/login/{login_id}/complete")
    async def complete_login(login_id: str, request: LoginCompleteRequest, raw_request: Request):
        rejection = _reject_unauthorized(raw_request, settings.api_key)
        if rejection is not None:
            return rejection
        try:
            token = await service.complete_login(login_id, request.value)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        if token is None:
            return JSONResponse(
                {"error": "unknown or expired login flow; start again"}, status_code=404
            )
        return {"ok": True, "token_id": token.id}

    return app
