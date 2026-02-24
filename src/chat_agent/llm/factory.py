from ..core.schema import (
    AnthropicConfig,
    CopilotConfig,
    GeminiConfig,
    LLMConfig,
    OllamaConfig,
    OpenAIConfig,
    OpenRouterConfig,
)
from .base import LLMClient
from .providers.anthropic import AnthropicClient
from .providers.copilot import CopilotClient
from .providers.gemini import GeminiClient
from .providers.ollama import OllamaClient
from .providers.openai import OpenAIClient
from .providers.openrouter import OpenRouterClient
from .retry import with_llm_retry


def _apply_request_timeout(
    config: LLMConfig,
    request_timeout: float | None,
) -> LLMConfig:
    if request_timeout is None:
        return config
    return config.model_copy(update={"request_timeout": request_timeout})


def create_client(
    config: LLMConfig,
    transient_retries: int = 0,
    request_timeout: float | None = None,
    rate_limit_retries: int = 0,
    force_agent: bool = False,
    retry_label: str | None = None,
) -> LLMClient:
    """Create LLM client based on provider config type."""
    config = _apply_request_timeout(config, request_timeout)
    client: LLMClient
    match config:
        case OllamaConfig():
            client = OllamaClient(config)
        case CopilotConfig():
            client = CopilotClient(config, force_agent=force_agent)
        case OpenAIConfig():
            client = OpenAIClient(config)
        case AnthropicConfig():
            client = AnthropicClient(config)
        case GeminiConfig():
            client = GeminiClient(config)
        case OpenRouterConfig():
            client = OpenRouterClient(config)
    return with_llm_retry(
        client,
        transient_retries,
        rate_limit_retries,
        label=retry_label,
    )
