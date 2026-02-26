from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..timezone_utils import validate_timezone_spec


class StrictConfigModel(BaseModel):
    """Shared strict config model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")


class ShellConfig(StrictConfigModel):
    """Shell execution configuration."""

    blacklist: list[str] = []
    timeout: int = 30
    export_env: list[str] = Field(default_factory=list)


class MemoryEditWarningsConfig(StrictConfigModel):
    """File health warning configuration for memory_edit."""

    max_lines: int = Field(default=75, ge=10)
    ignore: list[str] = Field(default_factory=lambda: [
        "recent.md",
        "index.md",
        "journal/",
    ])


class MemoryEditToolConfig(StrictConfigModel):
    """Configuration for memory_edit tool."""

    allow_failure: bool = False
    turn_retry_limit: int = Field(default=3, ge=1)
    warnings: MemoryEditWarningsConfig = Field(
        default_factory=MemoryEditWarningsConfig
    )


class BM25SearchConfig(StrictConfigModel):
    """BM25 deterministic search configuration."""

    top_k: int = Field(default=8, ge=1)
    snippet_lines: int = Field(default=3, ge=0)
    max_snippets_per_file: int = Field(default=3, ge=1)
    max_response_chars: int = Field(default=2000, ge=100)
    date_normalization: bool = True


class MemorySearchAgentConfig(StrictConfigModel):
    """Configuration for LLM-based memory search fallback."""

    allow_failure: bool = True


class MemorySearchToolConfig(StrictConfigModel):
    """Configuration for memory_search tool."""

    bm25: BM25SearchConfig = Field(default_factory=BM25SearchConfig)
    agent: MemorySearchAgentConfig = Field(default_factory=MemorySearchAgentConfig)


class ScrollConfig(StrictConfigModel):
    """Scroll behavior configuration."""

    invert: bool = False
    max_amount: int = Field(default=5, ge=1)


class ToolsConfig(StrictConfigModel):
    """Tools configuration for agent capabilities."""

    max_tool_iterations: int = Field(default=10, ge=1)
    allowed_paths: list[str] = []
    shell: ShellConfig = Field(default_factory=ShellConfig)
    memory_edit: MemoryEditToolConfig = Field(default_factory=MemoryEditToolConfig)
    memory_search: MemorySearchToolConfig = Field(default_factory=MemorySearchToolConfig)
    scroll: ScrollConfig = Field(default_factory=ScrollConfig)


# === Provider-specific reasoning/thinking configs ===
# Each provider has its own reasoning field type, matching its real API format.
# No shared ReasoningConfig — see docs/dev/provider-api-spec.md for API facts.


class OllamaReasoningConfig(StrictConfigModel):
    """Ollama reasoning config.

    Ollama's OpenAI-compat endpoint does NOT officially document reasoning_effort.
    This adapter sends it empirically. The native Ollama API uses 'think'
    (boolean or level string). See docs/dev/provider-api-spec.md.
    """

    enabled: bool | None = None
    effort: str | None = None
    # max_tokens not supported by Ollama


class OllamaCapabilities(StrictConfigModel):
    """Ollama model capabilities."""

    reasoning: "OllamaReasoningCapabilities"
    vision: bool = False


class OllamaReasoningCapabilities(StrictConfigModel):
    supports_toggle: bool
    supported_efforts: list[str] = Field(default_factory=list)
    supports_max_tokens: bool


class OllamaConfig(StrictConfigModel):
    """Ollama provider configuration."""

    provider: Literal["ollama"] = "ollama"
    model: str
    base_url: str = "http://localhost:11434/v1"
    max_tokens: int | None = None
    request_timeout: float = Field(default=120.0, gt=0)
    temperature: float | None = None
    reasoning: OllamaReasoningConfig | None = None
    capabilities: OllamaCapabilities | None = None
    provider_overrides: dict[str, Any] | None = None

    def validate_reasoning(self, *, source_path: Path) -> "OllamaConfig":
        reasoning = self.reasoning
        if reasoning is None:
            return self
        ctx = f"(provider={self.provider}, model={self.model}, path={source_path})"
        # Normalize: effort set -> enabled=True
        enabled = reasoning.enabled
        if reasoning.effort is not None and enabled is None:
            enabled = True
            reasoning = reasoning.model_copy(update={"enabled": enabled})
        if enabled is False and reasoning.effort is not None:
            raise ValueError("reasoning.effort cannot be set when enabled is false " + ctx)
        if self.capabilities is None:
            raise ValueError(
                "reasoning is configured but capabilities.reasoning is missing " + ctx
            )
        caps = self.capabilities.reasoning
        if reasoning.enabled is not None and not caps.supports_toggle:
            raise ValueError(
                "reasoning.enabled is set, but supports_toggle=false " + ctx
            )
        if reasoning.effort is not None and reasoning.effort not in caps.supported_efforts:
            allowed = ", ".join(caps.supported_efforts) or "(none)"
            raise ValueError(
                f"reasoning.effort={reasoning.effort!r} is not supported "
                f"(supported_efforts={allowed}) {ctx}"
            )
        return self.model_copy(update={"reasoning": reasoning})

    def get_vision(self) -> bool:
        return bool(self.capabilities and self.capabilities.vision)

    def create_client(self) -> Any:
        from ..llm.providers.ollama import OllamaClient
        return OllamaClient(self)


class CopilotReasoningConfig(StrictConfigModel):
    """Copilot reasoning config.

    Copilot /chat/completions uses top-level reasoning_effort (empirical/
    reverse-engineered via copilot-api compatibility behavior).
    Endpoint and payload format are historical/reverse-engineered.
    See docs/dev/provider-api-spec.md.
    """

    enabled: bool | None = None
    effort: str | None = None
    supported_efforts: list[str] = Field(default_factory=list)


class CopilotConfig(StrictConfigModel):
    """Copilot proxy (OpenAI-compatible, no auth)."""

    provider: Literal["copilot"] = "copilot"
    model: str
    base_url: str = "http://localhost:4141/v1"
    max_tokens: int | None = None
    request_timeout: float | None = None
    temperature: float | None = None
    vision: bool = False
    reasoning: CopilotReasoningConfig | None = None

    def validate_reasoning(self, *, source_path: Path) -> "CopilotConfig":
        reasoning = self.reasoning
        if reasoning is None:
            return self
        ctx = f"(provider={self.provider}, model={self.model}, path={source_path})"
        enabled = reasoning.enabled
        if reasoning.effort is not None and enabled is None:
            enabled = True
            reasoning = reasoning.model_copy(update={"enabled": enabled})
        if enabled is False and reasoning.effort is not None:
            raise ValueError("reasoning.effort cannot be set when enabled is false " + ctx)
        if reasoning.effort is not None and reasoning.effort not in reasoning.supported_efforts:
            allowed = ", ".join(reasoning.supported_efforts) or "(none)"
            raise ValueError(
                f"reasoning.effort={reasoning.effort!r} is not supported "
                f"(supported_efforts={allowed}) {ctx}"
            )
        return self.model_copy(update={"reasoning": reasoning})

    def get_vision(self) -> bool:
        return self.vision

    def create_client(self, *, force_agent: bool = False) -> Any:
        from ..llm.providers.copilot import CopilotClient
        return CopilotClient(self, force_agent=force_agent)


class OpenAIReasoningConfig(StrictConfigModel):
    """OpenAI Chat Completions reasoning config.

    Chat Completions API uses reasoning_effort (top-level string field).
    Responses API uses reasoning: {"effort": ...} object — NOT used here.
    See docs/dev/provider-api-spec.md.
    """

    enabled: bool | None = None
    effort: str | None = None
    # max_tokens not supported by OpenAI Chat Completions for reasoning


class OpenAICapabilities(StrictConfigModel):
    reasoning: "OpenAIReasoningCapabilities"
    vision: bool = False


class OpenAIReasoningCapabilities(StrictConfigModel):
    supports_toggle: bool
    supported_efforts: list[str] = Field(default_factory=list)
    supports_max_tokens: bool


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
    reasoning: OpenAIReasoningConfig | None = None
    capabilities: OpenAICapabilities | None = None
    provider_overrides: dict[str, Any] | None = None

    def validate_reasoning(self, *, source_path: Path) -> "OpenAIConfig":
        reasoning = self.reasoning
        if reasoning is None:
            return self
        ctx = f"(provider={self.provider}, model={self.model}, path={source_path})"
        enabled = reasoning.enabled
        if reasoning.effort is not None and enabled is None:
            enabled = True
            reasoning = reasoning.model_copy(update={"enabled": enabled})
        if enabled is False and reasoning.effort is not None:
            raise ValueError("reasoning.effort cannot be set when enabled is false " + ctx)
        if self.capabilities is None:
            raise ValueError(
                "reasoning is configured but capabilities.reasoning is missing " + ctx
            )
        caps = self.capabilities.reasoning
        if reasoning.enabled is not None and not caps.supports_toggle:
            raise ValueError(
                "reasoning.enabled is set, but supports_toggle=false " + ctx
            )
        if reasoning.effort is not None and reasoning.effort not in caps.supported_efforts:
            allowed = ", ".join(caps.supported_efforts) or "(none)"
            raise ValueError(
                f"reasoning.effort={reasoning.effort!r} is not supported "
                f"(supported_efforts={allowed}) {ctx}"
            )
        # OpenAI adapter constraints
        overrides = self.provider_overrides or {}
        if reasoning.enabled is False and overrides.get("openai_reasoning_effort") is None:
            raise ValueError(
                "OpenAI Chat Completions does not support reasoning.enabled=false "
                "without provider_overrides.openai_reasoning_effort " + ctx
            )
        return self.model_copy(update={"reasoning": reasoning})

    def get_vision(self) -> bool:
        return bool(self.capabilities and self.capabilities.vision)

    def create_client(self) -> Any:
        from ..llm.providers.openai import OpenAIClient
        return OpenAIClient(self)


class AnthropicThinkingConfig(StrictConfigModel):
    """Anthropic thinking config.

    Maps to thinking: {"type": "enabled", "budget_tokens": N} (manual mode).
    Adaptive thinking (type: "adaptive") and output_config.effort are NOT yet
    supported by this adapter. See docs/dev/provider-api-spec.md.
    """

    enabled: bool | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    # effort is NOT supported: Anthropic API has output_config.effort,
    # which is a different concept from reasoning effort. Not yet implemented.


class AnthropicCapabilities(StrictConfigModel):
    reasoning: "AnthropicReasoningCapabilities"
    vision: bool = False


class AnthropicReasoningCapabilities(StrictConfigModel):
    supports_toggle: bool
    supported_efforts: list[str] = Field(default_factory=list)
    supports_max_tokens: bool


class AnthropicConfig(StrictConfigModel):
    """Anthropic provider configuration.

    Uses thinking: {"type": "enabled", "budget_tokens": N} (manual mode).
    Adaptive thinking and output_config.effort are NOT yet supported.
    See docs/dev/provider-api-spec.md.
    """

    provider: Literal["anthropic"] = "anthropic"
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str = "https://api.anthropic.com"
    max_tokens: int = 4096
    request_timeout: float = Field(default=120.0, gt=0)
    temperature: float | None = None
    reasoning: AnthropicThinkingConfig | None = None
    capabilities: AnthropicCapabilities | None = None
    provider_overrides: dict[str, Any] | None = None

    def validate_reasoning(self, *, source_path: Path) -> "AnthropicConfig":
        reasoning = self.reasoning
        if reasoning is None:
            return self
        ctx = f"(provider={self.provider}, model={self.model}, path={source_path})"
        if reasoning.enabled is False and reasoning.max_tokens is not None:
            raise ValueError("reasoning.max_tokens cannot be set when enabled is false " + ctx)
        if self.capabilities is None:
            raise ValueError(
                "reasoning is configured but capabilities.reasoning is missing " + ctx
            )
        caps = self.capabilities.reasoning
        if reasoning.enabled is not None and not caps.supports_toggle:
            raise ValueError(
                "reasoning.enabled is set, but supports_toggle=false " + ctx
            )
        if reasoning.max_tokens is not None and not caps.supports_max_tokens:
            raise ValueError(
                "reasoning.max_tokens is set, but supports_max_tokens=false " + ctx
            )
        overrides = self.provider_overrides or {}
        if reasoning.enabled is True and (
            reasoning.max_tokens is None
            and overrides.get("anthropic_thinking") is None
            and overrides.get("anthropic_thinking_budget_tokens") is None
        ):
            raise ValueError(
                "Anthropic thinking requires reasoning.max_tokens or "
                "provider_overrides.anthropic_thinking_budget_tokens " + ctx
            )
        return self

    def get_vision(self) -> bool:
        return bool(self.capabilities and self.capabilities.vision)

    def create_client(self) -> Any:
        from ..llm.providers.anthropic import AnthropicClient
        return AnthropicClient(self)


class GeminiThinkingConfig(StrictConfigModel):
    """Gemini thinking config.

    Gemini 3: thinkingLevel (minimal/low/medium/high, model-dependent).
    Gemini 2.5: thinkingBudget (token count, 0=off, -1=dynamic).
    This adapter maps effort -> thinkingLevel and max_tokens -> thinkingBudget.
    'minimal' is NOT yet mapped. enabled=False sets thinkingBudget=0, which is
    invalid for Gemini 3 Pro. See docs/dev/provider-api-spec.md.
    """

    enabled: bool | None = None
    effort: str | None = None
    max_tokens: int | None = Field(default=None, gt=0)


class GeminiCapabilities(StrictConfigModel):
    reasoning: "GeminiReasoningCapabilities"
    vision: bool = False


class GeminiReasoningCapabilities(StrictConfigModel):
    supports_toggle: bool
    supported_efforts: list[str] = Field(default_factory=list)
    supports_max_tokens: bool


class GeminiConfig(StrictConfigModel):
    """Gemini provider configuration.

    See GeminiThinkingConfig docstring and docs/dev/provider-api-spec.md.
    """

    provider: Literal["gemini"] = "gemini"
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str = "https://generativelanguage.googleapis.com"
    max_tokens: int = 8192
    request_timeout: float = Field(default=120.0, gt=0)
    temperature: float | None = None
    reasoning: GeminiThinkingConfig | None = None
    capabilities: GeminiCapabilities | None = None
    provider_overrides: dict[str, Any] | None = None

    def validate_reasoning(self, *, source_path: Path) -> "GeminiConfig":
        reasoning = self.reasoning
        if reasoning is None:
            return self
        ctx = f"(provider={self.provider}, model={self.model}, path={source_path})"
        enabled = reasoning.enabled
        if reasoning.effort is not None and enabled is None:
            enabled = True
            reasoning = reasoning.model_copy(update={"enabled": enabled})
        if enabled is False and reasoning.effort is not None:
            raise ValueError("reasoning.effort cannot be set when enabled is false " + ctx)
        if enabled is False and reasoning.max_tokens is not None:
            raise ValueError("reasoning.max_tokens cannot be set when enabled is false " + ctx)
        if self.capabilities is None:
            raise ValueError(
                "reasoning is configured but capabilities.reasoning is missing " + ctx
            )
        caps = self.capabilities.reasoning
        if reasoning.enabled is not None and not caps.supports_toggle:
            raise ValueError(
                "reasoning.enabled is set, but supports_toggle=false " + ctx
            )
        if reasoning.effort is not None and reasoning.effort not in caps.supported_efforts:
            allowed = ", ".join(caps.supported_efforts) or "(none)"
            raise ValueError(
                f"reasoning.effort={reasoning.effort!r} is not supported "
                f"(supported_efforts={allowed}) {ctx}"
            )
        if reasoning.max_tokens is not None and not caps.supports_max_tokens:
            raise ValueError(
                "reasoning.max_tokens is set, but supports_max_tokens=false " + ctx
            )
        return self.model_copy(update={"reasoning": reasoning})

    def get_vision(self) -> bool:
        return bool(self.capabilities and self.capabilities.vision)

    def create_client(self) -> Any:
        from ..llm.providers.gemini import GeminiClient
        return GeminiClient(self)


class OpenRouterReasoningConfig(StrictConfigModel):
    """OpenRouter reasoning config.

    Uses reasoning: {"effort": ...} object format.
    Supports effort and max_tokens (adapter rule: effort takes precedence).
    Precedence is NOT officially specified by OpenRouter.
    See docs/dev/provider-api-spec.md.
    """

    enabled: bool | None = None
    effort: str | None = None
    max_tokens: int | None = Field(default=None, gt=0)


class OpenRouterCapabilities(StrictConfigModel):
    reasoning: "OpenRouterReasoningCapabilities"
    vision: bool = False


class OpenRouterReasoningCapabilities(StrictConfigModel):
    supports_toggle: bool
    supported_efforts: list[str] = Field(default_factory=list)
    supports_max_tokens: bool


class OpenRouterConfig(StrictConfigModel):
    """OpenRouter provider configuration.

    See OpenRouterReasoningConfig docstring and docs/dev/provider-api-spec.md.
    """

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
    reasoning: OpenRouterReasoningConfig | None = None
    capabilities: OpenRouterCapabilities | None = None
    provider_overrides: dict[str, Any] | None = None

    def validate_reasoning(self, *, source_path: Path) -> "OpenRouterConfig":
        reasoning = self.reasoning
        if reasoning is None:
            return self
        ctx = f"(provider={self.provider}, model={self.model}, path={source_path})"
        enabled = reasoning.enabled
        if reasoning.effort is not None and enabled is None:
            enabled = True
            reasoning = reasoning.model_copy(update={"enabled": enabled})
        if enabled is False and reasoning.effort is not None:
            raise ValueError("reasoning.effort cannot be set when enabled is false " + ctx)
        if enabled is False and reasoning.max_tokens is not None:
            raise ValueError("reasoning.max_tokens cannot be set when enabled is false " + ctx)
        if self.capabilities is None:
            raise ValueError(
                "reasoning is configured but capabilities.reasoning is missing " + ctx
            )
        caps = self.capabilities.reasoning
        if reasoning.enabled is not None and not caps.supports_toggle:
            raise ValueError(
                "reasoning.enabled is set, but supports_toggle=false " + ctx
            )
        if reasoning.effort is not None and reasoning.effort not in caps.supported_efforts:
            allowed = ", ".join(caps.supported_efforts) or "(none)"
            raise ValueError(
                f"reasoning.effort={reasoning.effort!r} is not supported "
                f"(supported_efforts={allowed}) {ctx}"
            )
        if reasoning.max_tokens is not None and not caps.supports_max_tokens:
            raise ValueError(
                "reasoning.max_tokens is set, but supports_max_tokens=false " + ctx
            )
        return self.model_copy(update={"reasoning": reasoning})

    def get_vision(self) -> bool:
        return bool(self.capabilities and self.capabilities.vision)

    def create_client(self) -> Any:
        from ..llm.providers.openrouter import OpenRouterClient
        return OpenRouterClient(self)


LLMConfig = Annotated[
    OllamaConfig | CopilotConfig | OpenAIConfig | AnthropicConfig | GeminiConfig | OpenRouterConfig,
    Field(discriminator="provider"),
]


class AgentConfig(StrictConfigModel):
    """Agent configuration with LLM settings."""

    enabled: bool = True
    llm: LLMConfig
    llm_request_timeout: float | None = Field(default=None, gt=0)
    llm_transient_retries: int = Field(default=1, ge=0)
    llm_rate_limit_retries: int = Field(default=5, ge=0)
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
    gui_instruction_max_chars: int | None = Field(default=None, ge=10)
    gui_text_max_chars: int | None = Field(default=None, ge=10)
    gui_worker_result_max_chars: int | None = Field(default=None, ge=10)
    gui_result_max_chars: int | None = Field(default=None, ge=10)
    # GUI screenshot optimization
    screenshot_max_width: int | None = Field(default=1280, ge=256)
    screenshot_quality: int = Field(default=80, ge=10, le=100)
    # Vision delegation: when False, delegate image reading to vision sub-agent
    use_own_vision_ability: bool = False


class MemoryArchiveConfig(StrictConfigModel):
    """Auto-archive rolling buffers older than retain_days."""

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

    class CommonGroundConfig(StrictConfigModel):
        """Time-anchored common-ground injection settings."""

        enabled: bool = True
        mode: Literal["auto_on_rev_mismatch"] = "auto_on_rev_mismatch"
        max_entries: int = Field(default=8, ge=1)
        max_chars: int = Field(default=1200, ge=100)
        max_entry_chars: int = Field(default=160, ge=20)
        persist_cache: bool = True
        rebuild_from_sessions_on_cache_miss: bool = True

    max_chars: int = Field(default=400_000, ge=10_000)
    preserve_turns: int = Field(default=6, ge=1)
    boot_files: list[str] = Field(default_factory=lambda: [
        "memory/agent/persona.md",
        "memory/agent/long-term.md",
        "memory/agent/skills/index.md",
    ])
    boot_files_as_tool: list[str] = Field(default_factory=lambda: [
        "memory/agent/index.md",
        "memory/agent/recent.md",
        "memory/agent/pending-thoughts.md",
        "memory/agent/interests/index.md",
    ])
    common_ground: CommonGroundConfig = Field(default_factory=CommonGroundConfig)


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


class DiscordListenChannel(StrictConfigModel):
    """Bootstrap/hard allowlist entry for a Discord guild channel."""

    channel_id: str
    filter: str = Field(
        default="mention_only",
        pattern=r"^(mention_only|all|from_contacts)$",
    )


class DiscordChannelConfig(StrictConfigModel):
    """Discord self-bot adapter settings."""

    enabled: bool = False
    debounce_seconds: int = Field(default=5, ge=1, le=30)
    max_wait_seconds: int = Field(default=30, ge=5, le=120)
    dm_debounce_seconds: int = Field(default=12, ge=1, le=300)
    dm_max_wait_seconds: int = Field(default=180, ge=5, le=600)
    dm_typing_quiet_seconds: int = Field(default=15, ge=2, le=120)
    send_delay_min: float = Field(default=1.0, ge=0)
    send_delay_max: float = Field(default=3.0, ge=0)
    listen_dms: bool = True
    listen_channels: list[DiscordListenChannel] = Field(default_factory=list)
    ignore_users: list[str] = Field(default_factory=list)
    guild_review_interval_seconds: int = Field(default=60, ge=5, le=3600)
    thinking_typing: bool = True
    thinking_typing_refresh_seconds: int = Field(default=7, ge=2, le=30)
    presence_mode: str = Field(default="auto", pattern=r"^(off|auto|keep_online)$")
    presence_refresh_seconds: int = Field(default=90, ge=10, le=600)
    presence_idle_after_seconds: int = Field(default=300, ge=30, le=3600)
    auto_read_images: bool = True
    auto_read_images_in_dm: bool = True
    auto_read_images_in_guild: bool = True
    auto_read_image_max_per_batch: int = Field(default=3, ge=0, le=20)
    auto_read_image_max_mb: int = Field(default=10, ge=1, le=200)

    @model_validator(mode="after")
    def _validate_ranges(self) -> "DiscordChannelConfig":
        if self.send_delay_min > self.send_delay_max:
            raise ValueError("send_delay_min must be <= send_delay_max")
        if self.debounce_seconds > self.max_wait_seconds:
            raise ValueError("debounce_seconds must be <= max_wait_seconds")
        if self.dm_debounce_seconds > self.dm_max_wait_seconds:
            raise ValueError("dm_debounce_seconds must be <= dm_max_wait_seconds")
        return self


class ChannelsConfig(StrictConfigModel):
    """Channel adapter configuration."""

    gmail: GmailChannelConfig = Field(default_factory=GmailChannelConfig)
    discord: DiscordChannelConfig = Field(default_factory=DiscordChannelConfig)
    line_crack: LineCrackChannelConfig = Field(
        default_factory=LineCrackChannelConfig,
    )


class HeartbeatConfig(StrictConfigModel):
    """Autonomous heartbeat configuration."""

    enabled: bool = False
    # Whether to enqueue an immediate [STARTUP] system wake-up on process start.
    enqueue_startup: bool = False
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
    timezone: str = "UTC+8"
    context: ContextConfig = Field(default_factory=ContextConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    control: ControlConfig = Field(default_factory=ControlConfig)
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    agents: dict[str, AgentConfig]

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        return validate_timezone_spec(value)

    def get_agent_os_dir(self) -> Path:
        """Get resolved agent OS directory path."""
        return Path(self.agent_os_dir).expanduser().resolve()
