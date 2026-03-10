"""Deterministic handoff rule evaluation for shell sessions."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re

from ..core.schema import ShellHandoffConfig, ShellHandoffRuleConfig

_URL_RE = re.compile(r"https?://\S+")


@dataclass(frozen=True, slots=True)
class ShellHandoffObservation:
    """Normalized shell session state used for rule evaluation."""

    tail_lines: tuple[str, ...]
    last_line: str
    process_alive: bool
    idle_seconds: float

    @property
    def tail_text(self) -> str:
        return "\n".join(self.tail_lines)

    @property
    def has_url(self) -> bool:
        return _URL_RE.search(self.tail_text) is not None


@dataclass(frozen=True, slots=True)
class CompiledShellHandoffRule:
    """Compiled matcher for one shell handoff rule."""

    id: str
    outcome: str
    any_text: tuple[re.Pattern[str], ...]
    all_text: tuple[re.Pattern[str], ...]
    require_url: bool
    prompt_suffix: tuple[str, ...]
    process_alive: bool | None
    idle_seconds_ge: float | None

    @classmethod
    def from_config(cls, rule: ShellHandoffRuleConfig) -> "CompiledShellHandoffRule":
        return cls(
            id=rule.id,
            outcome=rule.outcome,
            any_text=tuple(re.compile(pattern) for pattern in rule.any_text),
            all_text=tuple(re.compile(pattern) for pattern in rule.all_text),
            require_url=rule.require_url,
            prompt_suffix=tuple(rule.prompt_suffix),
            process_alive=rule.process_alive,
            idle_seconds_ge=rule.idle_seconds_ge,
        )

    def matches(self, observation: ShellHandoffObservation) -> bool:
        """Return True when the observation satisfies every configured matcher."""
        if self.require_url and not observation.has_url:
            return False
        if self.process_alive is not None and observation.process_alive != self.process_alive:
            return False
        if (
            self.idle_seconds_ge is not None
            and observation.idle_seconds < self.idle_seconds_ge
        ):
            return False

        tail_text = observation.tail_text
        if self.any_text and not any(pattern.search(tail_text) for pattern in self.any_text):
            return False
        if self.all_text and not all(pattern.search(tail_text) for pattern in self.all_text):
            return False
        if self.prompt_suffix:
            stripped = observation.last_line.rstrip()
            if not any(stripped.endswith(suffix) for suffix in self.prompt_suffix):
                return False

        return True


class ShellHandoffEvaluator:
    """Evaluate shell session observations against configured handoff rules."""

    def __init__(
        self,
        *,
        enabled: bool,
        tail_lines: int,
        grace_seconds: float,
        rules: Iterable[CompiledShellHandoffRule],
    ) -> None:
        self.enabled = enabled
        self.tail_lines = tail_lines
        self.grace_seconds = grace_seconds
        self.rules = tuple(rules)

    @classmethod
    def from_config(cls, config: ShellHandoffConfig) -> "ShellHandoffEvaluator":
        return cls(
            enabled=config.enabled,
            tail_lines=config.tail_lines,
            grace_seconds=config.grace_seconds,
            rules=tuple(CompiledShellHandoffRule.from_config(rule) for rule in config.rules),
        )

    def evaluate(
        self,
        observation: ShellHandoffObservation,
    ) -> CompiledShellHandoffRule | None:
        """Return the first matching rule, if any."""
        if not self.enabled:
            return None
        for rule in self.rules:
            if rule.matches(observation):
                return rule
        return None
