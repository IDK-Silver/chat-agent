from ...core.schema import CopilotConfig
from ..schema import Message, OpenAIMessagePayload
from .openai_compat import OpenAICompatibleClient


class CopilotClient(OpenAICompatibleClient):
    """Client for copilot proxy (OpenAI-compatible, no auth)."""

    def __init__(self, config: CopilotConfig, *, force_agent: bool = False):
        effort = config.reasoning.effort if config.reasoning else None
        super().__init__(
            model=config.model,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            request_timeout=config.request_timeout,
            # Copilot /chat/completions follows the copilot-api compatibility
            # contract here: reasoning_effort is a top-level string.
            reasoning_effort=effort,
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
