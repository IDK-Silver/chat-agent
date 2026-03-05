"""LiteLLM proxy provider client (OpenAI-compatible).

LiteLLM is an OpenAI-compatible proxy that translates requests to
various backend models. Auth uses Bearer token. Reasoning is
pass-through (no validation — LiteLLM handles backend translation).
"""

from ...core.schema import LiteLLMConfig
from .openai_compat import OpenAICompatibleClient


class LiteLLMClient(OpenAICompatibleClient):
    def __init__(self, config: LiteLLMConfig):
        self.api_key = config.api_key
        super().__init__(
            model=config.model,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            request_timeout=config.request_timeout,
            reasoning_effort=config.reasoning_effort,
            temperature=config.temperature,
        )

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
