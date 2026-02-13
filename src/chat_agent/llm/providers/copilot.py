from ...core.schema import CopilotConfig
from ..reasoning import map_ollama_reasoning_effort
from .openai_compat import OpenAICompatibleClient


class CopilotClient(OpenAICompatibleClient):
    """Client for copilot-api proxy (OpenAI-compatible, no auth)."""

    def __init__(self, config: CopilotConfig):
        super().__init__(
            model=config.model,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            request_timeout=config.request_timeout,
            reasoning_effort=map_ollama_reasoning_effort(
                config.reasoning,
                provider_overrides=config.provider_overrides,
            ),
        )
