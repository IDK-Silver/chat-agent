from ...core.schema import OpenRouterConfig
from ..reasoning import map_openrouter_reasoning
from .openai_compat import OpenAICompatibleClient


class OpenRouterClient(OpenAICompatibleClient):
    def __init__(self, config: OpenRouterConfig):
        self.api_key = config.api_key
        self.site_url = config.site_url
        self.site_name = config.site_name
        super().__init__(
            model=config.model,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            request_timeout=config.request_timeout,
            reasoning_payload=map_openrouter_reasoning(
                config.reasoning,
                provider_overrides=config.provider_overrides,
            ),
            temperature=config.temperature,
        )

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.site_name:
            headers["X-Title"] = self.site_name
        return headers
