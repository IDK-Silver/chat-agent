from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictConfigModel(BaseModel):
    """Shared strict config model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")


class ShellConfig(StrictConfigModel):
    """Shell execution configuration."""

    blacklist: list[str] = []
    timeout: int = 30


class ToolsConfig(StrictConfigModel):
    """Tools configuration for agent capabilities."""

    allowed_paths: list[str] = []
    shell: ShellConfig = Field(default_factory=ShellConfig)


class ReasoningConfig(StrictConfigModel):
    """Unified reasoning/thinking controls."""

    enabled: bool | None = None
    effort: Literal["low", "medium", "high"] | None = None
    max_tokens: int | None = Field(default=None, gt=0)


class ReasoningCapabilities(StrictConfigModel):
    """Per-model reasoning support matrix declared by profile YAML."""

    supports_toggle: bool
    supported_efforts: list[Literal["low", "medium", "high"]] = Field(default_factory=list)
    supports_max_tokens: bool


class LLMCapabilities(StrictConfigModel):
    """Capabilities block for LLM profiles."""

    reasoning: ReasoningCapabilities


class OllamaConfig(StrictConfigModel):
    """Ollama provider configuration."""

    provider: Literal["ollama"] = "ollama"
    model: str
    base_url: str = "http://localhost:11434/v1"
    max_tokens: int | None = None
    request_timeout: float = Field(default=120.0, gt=0)
    reasoning: ReasoningConfig | None = None
    capabilities: LLMCapabilities | None = None
    provider_overrides: dict[str, Any] | None = None


class OpenAIConfig(StrictConfigModel):
    """OpenAI provider configuration."""

    provider: Literal["openai"] = "openai"
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str = "https://api.openai.com/v1"
    max_tokens: int = 4096
    request_timeout: float = Field(default=120.0, gt=0)
    reasoning: ReasoningConfig | None = None
    capabilities: LLMCapabilities | None = None
    provider_overrides: dict[str, Any] | None = None


class AnthropicConfig(StrictConfigModel):
    """Anthropic provider configuration."""

    provider: Literal["anthropic"] = "anthropic"
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str = "https://api.anthropic.com"
    max_tokens: int = 4096
    request_timeout: float = Field(default=120.0, gt=0)
    reasoning: ReasoningConfig | None = None
    capabilities: LLMCapabilities | None = None
    provider_overrides: dict[str, Any] | None = None


class GeminiConfig(StrictConfigModel):
    """Gemini provider configuration."""

    provider: Literal["gemini"] = "gemini"
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str = "https://generativelanguage.googleapis.com"
    max_tokens: int = 8192
    request_timeout: float = Field(default=120.0, gt=0)
    reasoning: ReasoningConfig | None = None
    capabilities: LLMCapabilities | None = None
    provider_overrides: dict[str, Any] | None = None


class OpenRouterConfig(StrictConfigModel):
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
    reasoning: ReasoningConfig | None = None
    capabilities: LLMCapabilities | None = None
    provider_overrides: dict[str, Any] | None = None


LLMConfig = Annotated[
    OllamaConfig | OpenAIConfig | AnthropicConfig | GeminiConfig | OpenRouterConfig,
    Field(discriminator="provider"),
]


class AgentConfig(StrictConfigModel):
    """Agent configuration with LLM and optional reviewer settings."""

    enabled: bool = True
    llm: LLMConfig
    llm_request_timeout: float | None = Field(default=None, gt=0)
    llm_timeout_retries: int = Field(default=1, ge=0)
    llm_429_retries: int = Field(default=5, ge=0)
    # Reviewer / memory_searcher specific
    max_post_retries: int = 5
    pre_parse_retries: int = Field(default=1, ge=0)
    post_parse_retries: int = Field(default=1, ge=0)
    context_bytes_limit: int | None = Field(default=None, gt=0)
    max_results: int | None = Field(default=None, gt=0)
    history_turns: int = Field(default=6, ge=1)
    history_turn_max_chars: int = Field(default=1200, ge=200)
    reply_max_chars: int = Field(default=3000, ge=200)
    tool_preview_max_chars: int = Field(default=180, ge=50)
    enforce_memory_path_constraints: bool = True
    allow_unresolved: bool = False
    warn_on_failure: bool = True


class AppConfig(StrictConfigModel):
    """Application configuration."""

    working_dir: str = "~/.agent"
    debug: bool = False
    show_tool_use: bool = False
    warn_on_failure: bool = True
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    agents: dict[str, AgentConfig]

    def get_working_dir(self) -> Path:
        """Get resolved working directory path."""
        return Path(self.working_dir).expanduser().resolve()
