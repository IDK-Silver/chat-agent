"""FastAPI app for the native Codex proxy."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from chat_agent.llm.schema import CodexNativeRequest

from .service import CodexProxyService, CodexUpstreamError
from .settings import CodexProxySettings


def create_app(settings: CodexProxySettings) -> FastAPI:
    app = FastAPI(title="chat-agent-codex-proxy", docs_url=None, redoc_url=None)
    service = CodexProxyService(settings)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

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
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)
        return JSONResponse(response.model_dump())

    return app
