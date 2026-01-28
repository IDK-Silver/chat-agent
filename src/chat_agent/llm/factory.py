from ..core.schema import (
    AnthropicConfig,
    GeminiConfig,
    LLMConfig,
    OllamaConfig,
    OpenAIConfig,
)
from .base import LLMClient
from .providers.anthropic import AnthropicClient
from .providers.gemini import GeminiClient
from .providers.ollama import OllamaClient
from .providers.openai import OpenAIClient


def create_client(config: LLMConfig) -> LLMClient:
    """Create LLM client based on provider config type."""
    match config:
        case OllamaConfig():
            return OllamaClient(config)
        case OpenAIConfig():
            return OpenAIClient(config)
        case AnthropicConfig():
            return AnthropicClient(config)
        case GeminiConfig():
            return GeminiClient(config)
