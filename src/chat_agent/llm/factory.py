from .base import LLMClient
from .providers.ollama import OllamaClient
from .providers.openai import OpenAIClient
from .providers.anthropic import AnthropicClient
from .providers.gemini import GeminiClient


def create_client(config: dict) -> LLMClient:
    """Create LLM client based on provider in config."""
    provider = config.get("provider")

    if provider == "ollama":
        return OllamaClient(config)
    elif provider == "openai":
        return OpenAIClient(config)
    elif provider == "anthropic":
        return AnthropicClient(config)
    elif provider == "gemini":
        return GeminiClient(config)
    else:
        raise ValueError(f"Unknown provider: {provider}")
