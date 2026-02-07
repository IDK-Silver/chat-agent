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
    request_timeout: float = Field(default=120.0, gt=0)


class OpenAIConfig(BaseModel):
    """OpenAI provider configuration."""

    provider: Literal["openai"] = "openai"
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str = "https://api.openai.com/v1"
    max_tokens: int = 4096
    request_timeout: float = Field(default=120.0, gt=0)


class AnthropicConfig(BaseModel):
    """Anthropic provider configuration."""

    provider: Literal["anthropic"] = "anthropic"
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str = "https://api.anthropic.com"
    max_tokens: int = 4096
    request_timeout: float = Field(default=120.0, gt=0)


class GeminiConfig(BaseModel):
    """Gemini provider configuration."""

    provider: Literal["gemini"] = "gemini"
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str = "https://generativelanguage.googleapis.com"
    max_tokens: int = 8192
    request_timeout: float = Field(default=120.0, gt=0)


class OpenRouterConfig(BaseModel):
    """OpenRouter provider configuration."""

    provider: Literal["openrouter"] = "openrouter"
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str = "https://openrouter.ai/api/v1"
    max_tokens: int = 4096
    request_timeout: float = Field(default=120.0, gt=0)
    # Optional headers for OpenRouter leaderboard identification
    site_url: str | None = None  # HTTP-Referer header
    site_name: str | None = None  # X-Title header


LLMConfig = Annotated[
    OllamaConfig | OpenAIConfig | AnthropicConfig | GeminiConfig | OpenRouterConfig,
    Field(discriminator="provider"),
]


class AgentConfig(BaseModel):
    """Agent configuration with LLM and optional reviewer settings."""

    llm: LLMConfig
    llm_request_timeout: float | None = Field(default=None, gt=0)
    llm_timeout_retries: int = Field(default=1, ge=0)
    # Reviewer-specific (only used by pre_reviewer / post_reviewer agents)
    max_prefetch_actions: int = 5
    max_files_per_grep: int = 3
    max_post_retries: int = 2
    shell_whitelist: list[str] = Field(
        default_factory=lambda: ["grep", "cat", "ls", "find", "wc"]
    )


class AppConfig(BaseModel):
    """Application configuration."""

    working_dir: str = "~/.agent"
    debug: bool = False
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    agents: dict[str, AgentConfig]

    def get_working_dir(self) -> Path:
        """Get resolved working directory path."""
        return Path(self.working_dir).expanduser().resolve()
