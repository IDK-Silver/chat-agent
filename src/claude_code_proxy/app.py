"""FastAPI app for the native Claude Code proxy."""

from __future__ import annotations

from starlette.background import BackgroundTask
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from chat_agent.llm.schema import ClaudeCodeRequest

from .service import ClaudeCodeProxyService, ClaudeCodeUpstreamError
from .settings import ClaudeCodeProxySettings


async def _close_stream(client, response) -> None:
    await response.aclose()
    await client.aclose()


def create_app(settings: ClaudeCodeProxySettings) -> FastAPI:
    app = FastAPI(title="chat-agent-claude-code-proxy", docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    service = ClaudeCodeProxyService(settings)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/messages")
    async def messages(request: ClaudeCodeRequest):
        try:
            if request.stream:
                client, upstream = await service.open_stream(request)
                return StreamingResponse(
                    upstream.aiter_raw(),
                    status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type", "text/event-stream"),
                    background=BackgroundTask(_close_stream, client, upstream),
                )

            body, media_type = await service.forward_json(request)
        except ClaudeCodeUpstreamError as exc:
            return Response(
                content=exc.body,
                status_code=exc.status_code,
                media_type=exc.media_type,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        return Response(content=body, media_type=media_type)

    return app
