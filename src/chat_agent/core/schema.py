from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class ShellConfig(BaseModel):
    """Shell execution configuration."""

    blacklist: list[str] = []
    timeout: int = 30


class ToolsConfig(BaseModel):
    """Tools configuration for agent capabilities."""

    allowed_paths: list[str] = []
    shell: ShellConfig = Field(default_factory=ShellConfig)


class OllamaConfig(BaseModel):
    """Ollama provider configuration."""

    provider: Literal["ollama"] = "ollama"
    model: str
    base_url: str = "http://localhost:11434"


class OpenAIConfig(BaseModel):
    """OpenAI provider configuration."""

    provider: Literal["openai"] = "openai"
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str = "https://api.openai.com/v1"
    max_tokens: int = 4096


class AnthropicConfig(BaseModel):
    """Anthropic provider configuration."""

    provider: Literal["anthropic"] = "anthropic"
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str = "https://api.anthropic.com"
    max_tokens: int = 4096


class GeminiConfig(BaseModel):
    """Gemini provider configuration."""

    provider: Literal["gemini"] = "gemini"
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str = "https://generativelanguage.googleapis.com"
    max_tokens: int = 8192


LLMConfig = Annotated[
    OllamaConfig | OpenAIConfig | AnthropicConfig | GeminiConfig,
    Field(discriminator="provider"),
]


class AgentConfig(BaseModel):
    """Agent configuration with LLM settings."""

    llm: LLMConfig


class AppConfig(BaseModel):
    """Application configuration."""

    working_dir: str = "~/.agent"
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    agents: dict[str, AgentConfig]

    def get_working_dir(self) -> Path:
        """Get resolved working directory path."""
        return Path(self.working_dir).expanduser().resolve()
