from ...core.schema import CopilotConfig
from ..reasoning import map_ollama_reasoning_effort
from ..schema import Message, OpenAIMessagePayload
from .openai_compat import OpenAICompatibleClient


class CopilotClient(OpenAICompatibleClient):
    """Client for copilot-api proxy (OpenAI-compatible, no auth)."""

    def __init__(self, config: CopilotConfig, *, force_agent: bool = False):
        super().__init__(
            model=config.model,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            request_timeout=config.request_timeout,
            reasoning_effort=map_ollama_reasoning_effort(
                config.reasoning,
                provider_overrides=config.provider_overrides,
            ),
            temperature=config.temperature,
        )
        self._force_agent = force_agent

    def _convert_messages(self, messages: list[Message]) -> list[OpenAIMessagePayload]:
        result = super()._convert_messages(messages)
        if not self._force_agent:
            return result
        # Inject an assistant message after the first system message so
        # copilot-api sees an assistant role and classifies as "agent".
        insert_idx = 1 if result and result[0].role == "system" else 0
        result.insert(insert_idx, OpenAIMessagePayload(role="assistant", content="."))
        return result
