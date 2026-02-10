"""Reasoning/thinking normalization, validation, and provider mappings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.schema import (
    AnthropicConfig,
    GeminiConfig,
    LLMConfig,
    OpenAIConfig,
    OllamaConfig,
    OpenRouterConfig,
    ReasoningConfig,
)

_GEMINI_EFFORT_TO_LEVEL = {
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
}


def normalize_reasoning(reasoning: ReasoningConfig | None) -> ReasoningConfig | None:
    """Normalize cross-provider reasoning flags into a consistent shape."""
    if reasoning is None:
        return None

    enabled = reasoning.enabled
    if reasoning.effort is not None and enabled is None:
        enabled = True

    if enabled is False and reasoning.effort is not None:
        raise ValueError("reasoning.effort cannot be set when reasoning.enabled is false")

    if enabled is False and reasoning.max_tokens is not None:
        raise ValueError("reasoning.max_tokens cannot be set when reasoning.enabled is false")

    return reasoning.model_copy(update={"enabled": enabled})


def validate_and_normalize_reasoning_config(
    config: LLMConfig,
    *,
    source_path: Path,
) -> LLMConfig:
    """Validate profile-level reasoning settings and return normalized config."""
    reasoning = normalize_reasoning(config.reasoning)
    if reasoning is None:
        return config

    ctx = _format_context(config, source_path)
    if config.capabilities is None:
        raise ValueError(
            "reasoning is configured but capabilities.reasoning is missing " + ctx
        )

    caps = config.capabilities.reasoning
    if reasoning.enabled is not None and not caps.supports_toggle:
        raise ValueError(
            "reasoning.enabled is set, but supports_toggle=false in capabilities " + ctx
        )

    if reasoning.effort is not None and reasoning.effort not in caps.supported_efforts:
        allowed = ", ".join(caps.supported_efforts) or "(none)"
        raise ValueError(
            f"reasoning.effort={reasoning.effort!r} is not supported "
            f"(supported_efforts={allowed}) {ctx}"
        )

    if reasoning.max_tokens is not None and not caps.supports_max_tokens:
        raise ValueError(
            "reasoning.max_tokens is set, but supports_max_tokens=false in capabilities "
            + ctx
        )

    _validate_provider_constraints(config, reasoning, source_path=source_path)
    return config.model_copy(update={"reasoning": reasoning})


def map_ollama_reasoning_effort(
    reasoning: ReasoningConfig | None,
    *,
    provider_overrides: dict[str, Any] | None = None,
) -> str | None:
    """Map unified reasoning config to OpenAI-compatible `reasoning_effort`."""
    override = _get_override(provider_overrides, "ollama_think")
    if override is not None:
        if isinstance(override, bool):
            return "medium" if override else None
        if isinstance(override, str) and override in {"low", "medium", "high"}:
            return override
        raise ValueError("provider_overrides.ollama_think must be bool or low/medium/high")

    reasoning = normalize_reasoning(reasoning)
    if reasoning is None:
        return None
    if reasoning.enabled is False:
        return None
    if reasoning.effort is not None:
        return reasoning.effort
    if reasoning.enabled is True:
        return "medium"
    return None


def map_openai_reasoning_effort(
    reasoning: ReasoningConfig | None,
    *,
    provider_overrides: dict[str, Any] | None = None,
) -> str | None:
    """Map unified reasoning config to OpenAI Chat Completions `reasoning_effort`."""
    override = _get_override(provider_overrides, "openai_reasoning_effort")
    if override is not None:
        if not isinstance(override, str) or not override.strip():
            raise ValueError("provider_overrides.openai_reasoning_effort must be a string")
        return override

    reasoning = normalize_reasoning(reasoning)
    if reasoning is None:
        return None
    if reasoning.effort is not None:
        return reasoning.effort
    return None


def map_openrouter_reasoning(
    reasoning: ReasoningConfig | None,
    *,
    provider_overrides: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Map unified reasoning config to OpenRouter `reasoning` object."""
    override = _get_override(provider_overrides, "openrouter_reasoning")
    if override is not None:
        if not isinstance(override, dict):
            raise ValueError("provider_overrides.openrouter_reasoning must be an object")
        return override

    reasoning = normalize_reasoning(reasoning)
    if reasoning is None or reasoning.enabled is False:
        return None

    payload: dict[str, Any] = {}
    # OpenRouter: use either effort OR max_tokens, not both
    if reasoning.effort is not None:
        payload["effort"] = reasoning.effort
    elif reasoning.max_tokens is not None:
        payload["max_tokens"] = reasoning.max_tokens
    return payload or None


def map_anthropic_thinking(
    reasoning: ReasoningConfig | None,
    *,
    provider_overrides: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Map unified reasoning config to Anthropic `thinking` object."""
    override = _get_override(provider_overrides, "anthropic_thinking")
    if override is not None:
        if not isinstance(override, dict):
            raise ValueError("provider_overrides.anthropic_thinking must be an object")
        return override

    reasoning = normalize_reasoning(reasoning)
    if reasoning is None or reasoning.enabled is False:
        return None

    budget_tokens = reasoning.max_tokens
    if budget_tokens is None:
        budget_override = _get_override(provider_overrides, "anthropic_thinking_budget_tokens")
        if budget_override is not None:
            if not isinstance(budget_override, int) or budget_override <= 0:
                raise ValueError(
                    "provider_overrides.anthropic_thinking_budget_tokens must be > 0"
                )
            budget_tokens = budget_override

    payload: dict[str, Any] = {"type": "enabled"}
    if budget_tokens is not None:
        payload["budget_tokens"] = budget_tokens
    return payload


def map_gemini_thinking_config(
    reasoning: ReasoningConfig | None,
    *,
    provider_overrides: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Map unified reasoning config to Gemini `generationConfig.thinkingConfig`."""
    override = _get_override(provider_overrides, "gemini_thinking_config")
    if override is not None:
        if not isinstance(override, dict):
            raise ValueError("provider_overrides.gemini_thinking_config must be an object")
        return override

    reasoning = normalize_reasoning(reasoning)
    if reasoning is None:
        return None

    payload: dict[str, Any] = {}
    if reasoning.enabled is False:
        payload["thinkingBudget"] = 0
        return payload

    if reasoning.max_tokens is not None:
        payload["thinkingBudget"] = reasoning.max_tokens
    if reasoning.effort is not None:
        payload["thinkingLevel"] = _GEMINI_EFFORT_TO_LEVEL[reasoning.effort]
    if reasoning.enabled is True and "thinkingBudget" not in payload:
        payload["thinkingBudget"] = 1024
    return payload or None


def _validate_provider_constraints(
    config: LLMConfig,
    reasoning: ReasoningConfig,
    *,
    source_path: Path,
) -> None:
    """Apply provider-specific constraints not covered by capabilities."""
    ctx = _format_context(config, source_path)
    overrides = config.provider_overrides

    if isinstance(config, OpenAIConfig):
        if reasoning.enabled is False and _get_override(overrides, "openai_reasoning_effort") is None:
            raise ValueError(
                "OpenAI Chat Completions does not support reasoning.enabled=false "
                "without provider_overrides.openai_reasoning_effort " + ctx
            )
        if reasoning.max_tokens is not None:
            raise ValueError(
                "OpenAI mapping does not support reasoning.max_tokens in this phase " + ctx
            )
        return

    if isinstance(config, AnthropicConfig):
        if reasoning.effort is not None:
            raise ValueError("Anthropic mapping does not support reasoning.effort " + ctx)
        if reasoning.enabled is True and (
            reasoning.max_tokens is None
            and _get_override(overrides, "anthropic_thinking") is None
            and _get_override(overrides, "anthropic_thinking_budget_tokens") is None
        ):
            raise ValueError(
                "Anthropic thinking requires reasoning.max_tokens or "
                "provider_overrides.anthropic_thinking_budget_tokens " + ctx
            )
        return

    if isinstance(config, GeminiConfig):
        return

    if isinstance(config, OpenRouterConfig):
        return

    if isinstance(config, OllamaConfig):
        return


def _get_override(provider_overrides: dict[str, Any] | None, key: str) -> Any:
    if provider_overrides is None:
        return None
    return provider_overrides.get(key)


def _format_context(config: LLMConfig, source_path: Path) -> str:
    return f"(provider={config.provider}, model={config.model}, path={source_path})"
