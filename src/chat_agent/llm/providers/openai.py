from ...core.schema import OpenAIConfig
from ..reasoning import map_openai_reasoning_effort
from .openai_compat import OpenAICompatibleClient


class OpenAIClient(OpenAICompatibleClient):
    def __init__(self, config: OpenAIConfig):
        self.api_key = config.api_key
        super().__init__(
            model=config.model,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            request_timeout=config.request_timeout,
            reasoning_effort=map_openai_reasoning_effort(
                config.reasoning,
                provider_overrides=config.provider_overrides,
            ),
            temperature=config.temperature,
        )

    def _get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
