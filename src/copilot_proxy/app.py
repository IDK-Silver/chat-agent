"""FastAPI app for the native Copilot proxy."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from chat_agent.llm.schema import CopilotNativeRequest

from .service import CopilotProxyService, CopilotUpstreamError
from .settings import CopilotProxySettings


def create_app(settings: CopilotProxySettings) -> FastAPI:
    app = FastAPI(title="chat-agent-copilot-proxy", docs_url=None, redoc_url=None)
    service = CopilotProxyService(settings)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/chat")
    async def chat(request: CopilotNativeRequest):
        try:
            response = await service.chat(request)
        except CopilotUpstreamError as exc:
            return Response(
                content=exc.body,
                status_code=exc.status_code,
                media_type="application/json",
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(response.model_dump())

    return app
