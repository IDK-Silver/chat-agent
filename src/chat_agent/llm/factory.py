from ..core.schema import (
    AnthropicConfig,
    GeminiConfig,
    LLMConfig,
    OllamaConfig,
    OpenAIConfig,
    OpenRouterConfig,
)
from .base import LLMClient
from .providers.anthropic import AnthropicClient
from .providers.gemini import GeminiClient
from .providers.ollama import OllamaClient
from .providers.openai import OpenAIClient
from .providers.openrouter import OpenRouterClient
from .retry import with_timeout_retry


def _apply_request_timeout(
    config: LLMConfig,
    request_timeout: float | None,
) -> LLMConfig:
    if request_timeout is None:
        return config
    return config.model_copy(update={"request_timeout": request_timeout})


def create_client(
    config: LLMConfig,
    timeout_retries: int = 0,
    request_timeout: float | None = None,
) -> LLMClient:
    """Create LLM client based on provider config type."""
    config = _apply_request_timeout(config, request_timeout)
    client: LLMClient
    match config:
        case OllamaConfig():
            client = OllamaClient(config)
        case OpenAIConfig():
            client = OpenAIClient(config)
        case AnthropicConfig():
            client = AnthropicClient(config)
        case GeminiConfig():
            client = GeminiClient(config)
        case OpenRouterConfig():
            client = OpenRouterClient(config)
    return with_timeout_retry(client, timeout_retries)
