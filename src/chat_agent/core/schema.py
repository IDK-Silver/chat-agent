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
    export_env: list[str] = Field(default_factory=list)


class MemoryEditToolConfig(StrictConfigModel):
    """Configuration for memory_edit tool failure behavior."""

    allow_failure: bool = False


class MemorySearchToolConfig(StrictConfigModel):
    """Configuration for memory_search tool failure behavior."""

    allow_failure: bool = True


class ScrollConfig(StrictConfigModel):
    """Scroll behavior configuration."""

    invert: bool = False
    max_amount: int = Field(default=5, ge=1)


class ToolsConfig(StrictConfigModel):
    """Tools configuration for agent capabilities."""

    allowed_paths: list[str] = []
    shell: ShellConfig = Field(default_factory=ShellConfig)
    memory_edit: MemoryEditToolConfig = Field(default_factory=MemoryEditToolConfig)
    memory_search: MemorySearchToolConfig = Field(default_factory=MemorySearchToolConfig)
    scroll: ScrollConfig = Field(default_factory=ScrollConfig)


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
    vision: bool = False


class OllamaConfig(StrictConfigModel):
    """Ollama provider configuration."""

    provider: Literal["ollama"] = "ollama"
    model: str
    base_url: str = "http://localhost:11434/v1"
    max_tokens: int | None = None
    request_timeout: float = Field(default=120.0, gt=0)
    temperature: float | None = None
    reasoning: ReasoningConfig | None = None
    capabilities: LLMCapabilities | None = None
    provider_overrides: dict[str, Any] | None = None


class CopilotConfig(StrictConfigModel):
    """Copilot-api proxy (OpenAI-compatible, no auth)."""

    provider: Literal["copilot"] = "copilot"
    model: str
    base_url: str = "http://localhost:4141/v1"
    max_tokens: int | None = None
    request_timeout: float = Field(default=120.0, gt=0)
    temperature: float | None = None
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
    temperature: float | None = None
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
    temperature: float | None = None
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
    temperature: float | None = None
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
    temperature: float | None = None
    # Optional headers for OpenRouter leaderboard identification
    site_url: str | None = None  # HTTP-Referer header
    site_name: str | None = None  # X-Title header
    reasoning: ReasoningConfig | None = None
    capabilities: LLMCapabilities | None = None
    provider_overrides: dict[str, Any] | None = None


LLMConfig = Annotated[
    OllamaConfig | CopilotConfig | OpenAIConfig | AnthropicConfig | GeminiConfig | OpenRouterConfig,
    Field(discriminator="provider"),
]


class AgentConfig(StrictConfigModel):
    """Agent configuration with LLM settings."""

    enabled: bool = True
    llm: LLMConfig
    llm_request_timeout: float | None = Field(default=None, gt=0)
    llm_timeout_retries: int = Field(default=1, ge=0)
    llm_429_retries: int = Field(default=5, ge=0)
    # Memory searcher / editor specific
    pre_parse_retries: int = Field(default=1, ge=0)
    post_parse_retries: int = Field(default=1, ge=0)
    context_bytes_limit: int | None = Field(default=None, gt=0)
    max_results: int | None = Field(default=None, gt=0)
    enforce_memory_path_constraints: bool = True
    warn_on_failure: bool = True
    # GUI manager specific
    max_steps: int = Field(default=20, ge=1)
    gui_intent_max_chars: int | None = Field(default=None, ge=10)
    gui_instruction_max_chars: int = Field(default=60, ge=10)
    gui_text_max_chars: int = Field(default=40, ge=10)
    gui_worker_result_max_chars: int = Field(default=100, ge=10)
    gui_result_max_chars: int = Field(default=60, ge=10)
    # GUI screenshot optimization
    screenshot_max_width: int | None = Field(default=1280, ge=256)
    screenshot_quality: int = Field(default=80, ge=10, le=100)
    # Vision delegation: when False, delegate image reading to vision sub-agent
    use_own_vision_ability: bool = False


class MemoryArchiveConfig(StrictConfigModel):
    """Auto-archive rolling buffers when they exceed max_lines."""

    max_lines: int = Field(default=300, ge=50)
    retain_days: int = Field(default=3, ge=1)


class MemoryBackupConfig(StrictConfigModel):
    """Periodic memory backup configuration."""

    enabled: bool = True
    interval_minutes: int = Field(default=30, ge=1)
    retention_minutes: int = Field(default=1440, ge=1)


class SessionCleanupConfig(StrictConfigModel):
    """Auto-cleanup expired sessions on graceful exit."""

    enabled: bool = True
    retention_days: int = Field(default=30, ge=1)


class ContextRefreshConfig(StrictConfigModel):
    """Periodic context refresh: compact conversation + reload boot files."""

    enabled: bool = True
    interval_hours: int = Field(default=6, ge=1)
    on_day_change: bool = True
    preserve_turns: int = Field(default=2, ge=0)


class HooksConfig(StrictConfigModel):
    """Lifecycle hooks configuration."""

    memory_archive: MemoryArchiveConfig = Field(default_factory=MemoryArchiveConfig)
    memory_backup: MemoryBackupConfig = Field(default_factory=MemoryBackupConfig)
    session_cleanup: SessionCleanupConfig = Field(default_factory=SessionCleanupConfig)
    context_refresh: ContextRefreshConfig = Field(default_factory=ContextRefreshConfig)


class ContextConfig(StrictConfigModel):
    """Context window management."""

    max_chars: int = Field(default=400_000, ge=10_000)
    preserve_turns: int = Field(default=6, ge=1)
    boot_files: list[str] = Field(default_factory=lambda: [
        "memory/agent/persona.md",
        "memory/agent/long-term.md",
        "memory/agent/skills/index.md",
    ])
    boot_files_as_tool: list[str] = Field(default_factory=lambda: [
        "memory/agent/inner-state.md",
        "memory/agent/short-term.md",
        "memory/agent/pending-thoughts.md",
        "memory/agent/interests/index.md",
    ])


class SessionConfig(StrictConfigModel):
    """Session persistence and resume display settings."""

    replay_turns: int | None = Field(default=5, ge=1)
    show_tool_calls: bool = True


class FeaturesConfig(StrictConfigModel):
    """Feature flags."""

    copilot_agent_hint: bool = False


class GmailChannelConfig(StrictConfigModel):
    """Gmail channel adapter settings."""

    enabled: bool = True
    poll_interval: int = Field(default=45, ge=1)
    max_age_minutes: int | None = Field(default=None, ge=1)
    ignore_senders: list[str] = Field(default_factory=list)
    thread_max_age_days: int = Field(default=7, ge=1)


class LineCrackChannelConfig(StrictConfigModel):
    """LINE Desktop crack adapter settings (macOS only)."""

    enabled: bool = False
    poll_interval: int = Field(default=30, ge=5)
    screenshot_max_width: int | None = Field(default=1280, ge=256)
    screenshot_quality: int = Field(default=80, ge=10, le=100)
    scroll_similarity_threshold: float = Field(default=0.995, ge=0.9, le=1.0)
    max_scroll_captures: int = Field(default=20, ge=1, le=100)


class ChannelsConfig(StrictConfigModel):
    """Channel adapter configuration."""

    gmail: GmailChannelConfig = Field(default_factory=GmailChannelConfig)
    line_crack: LineCrackChannelConfig = Field(
        default_factory=LineCrackChannelConfig,
    )


class HeartbeatConfig(StrictConfigModel):
    """Autonomous heartbeat configuration."""

    enabled: bool = False
    # Supports hours (h) or minutes (m), e.g. "2h-5h", "30m-90m"
    interval: str = Field(
        default="2h-5h", pattern=r"^\d+[hm]-\d+[hm]$"
    )


class ControlConfig(StrictConfigModel):
    """Control API server configuration for external process management."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=9001, ge=1, le=65535)


class AppConfig(StrictConfigModel):
    """Application configuration."""

    agent_os_dir: str = "~/.agent"
    debug: bool = False
    show_tool_use: bool = False
    warn_on_failure: bool = True
    context: ContextConfig = Field(default_factory=ContextConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    control: ControlConfig = Field(default_factory=ControlConfig)
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    agents: dict[str, AgentConfig]

    def get_agent_os_dir(self) -> Path:
        """Get resolved agent OS directory path."""
        return Path(self.agent_os_dir).expanduser().resolve()
