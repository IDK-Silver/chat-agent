"""Runtime request routing for Copilot premium/non-premium initiators."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from ...agent.schema import InboundMessage
from ...core.schema import CopilotInboundRuleConfig, CopilotInitiatorPolicyConfig

CopilotDispatchMode = Literal["first_user_then_agent", "always_agent"]
CopilotEntryMode = Literal["human_entry", "agent_entry"]
CopilotInitiator = Literal["user", "agent"]
CopilotInteractionType = Literal["conversation-agent", "conversation-subagent"]

_INTERNAL_AGENT_CHANNELS = {"system", "gui", "shell_task"}
_DISCORD_REVIEW_SOURCES = {"guild_review", "guild_mention_review"}
_AGENT_ONLY_METADATA_KEYS = {
    "pre_sleep_sync",
    "scheduled_reason",
    "turn_failure_requeue_count",
    "yield_reschedule_count",
}
_DEFAULT_HUMAN_CHANNELS = {"cli", "gmail", "line"}


@dataclass
class CopilotRequestRouting:
    initiator: CopilotInitiator
    interaction_id: str
    interaction_type: CopilotInteractionType
    request_id: str


@dataclass
class _InboundScopeState:
    entry_mode: CopilotEntryMode
    interaction_id: str
    human_request_consumed: bool = False


class CopilotRuntime:
    """Classify inbound turns and derive Copilot request initiators."""

    def __init__(self, policy: CopilotInitiatorPolicyConfig):
        self._policy = policy
        self._scope: ContextVar[_InboundScopeState | None] = ContextVar(
            "copilot_inbound_scope",
            default=None,
        )

    @contextmanager
    def inbound_scope(self, msg: InboundMessage):
        state = _InboundScopeState(
            entry_mode=self.classify_inbound(msg),
            interaction_id=self._new_id(),
        )
        token = self._scope.set(state)
        try:
            yield state
        finally:
            self._scope.reset(token)

    def classify_inbound(self, msg: InboundMessage) -> CopilotEntryMode:
        metadata = msg.metadata or {}
        explicit = metadata.get("copilot_entry")
        if explicit == "human":
            return "human_entry"
        if explicit == "agent":
            return "agent_entry"
        if self._is_internal_agent_inbound(msg):
            return "agent_entry"
        if self._matches_human_entry(msg):
            return "human_entry"
        return "agent_entry"

    def resolve_request(self, dispatch_mode: CopilotDispatchMode) -> CopilotRequestRouting:
        interaction_type: CopilotInteractionType = (
            "conversation-agent"
            if dispatch_mode == "first_user_then_agent"
            else "conversation-subagent"
        )
        state = self._scope.get()
        interaction_id = state.interaction_id if state is not None else self._new_id()
        request_id = self._new_id()
        initiator: CopilotInitiator = "agent"

        if dispatch_mode == "always_agent":
            initiator = "agent"
        elif state is None:
            initiator = "user"
        elif state.entry_mode == "human_entry" and not state.human_request_consumed:
            state.human_request_consumed = True
            initiator = "user"
        else:
            initiator = "agent"

        return CopilotRequestRouting(
            initiator=initiator,
            interaction_id=interaction_id,
            interaction_type=interaction_type,
            request_id=request_id,
        )

    def _matches_human_entry(self, msg: InboundMessage) -> bool:
        if self._policy.use_default_human_entry_rules and self._matches_default_human_entry(msg):
            return True
        return any(self._rule_matches(msg, rule) for rule in self._policy.human_entry_rules)

    @staticmethod
    def _matches_default_human_entry(msg: InboundMessage) -> bool:
        if msg.channel in _DEFAULT_HUMAN_CHANNELS:
            return True
        if msg.channel != "discord":
            return False
        metadata = msg.metadata or {}
        source = metadata.get("source")
        if isinstance(source, str) and source == "dm_immediate":
            return True
        return bool(metadata.get("is_dm")) and source not in _DISCORD_REVIEW_SOURCES

    @staticmethod
    def _rule_matches(msg: InboundMessage, rule: CopilotInboundRuleConfig) -> bool:
        if msg.channel != rule.channel:
            return False
        if rule.sender is not None and msg.sender != rule.sender:
            return False
        metadata = msg.metadata or {}
        for key, expected in rule.metadata_equals.items():
            if metadata.get(key) != expected:
                return False
        return True

    @staticmethod
    def _is_internal_agent_inbound(msg: InboundMessage) -> bool:
        if msg.channel in _INTERNAL_AGENT_CHANNELS:
            return True
        metadata = msg.metadata or {}
        if bool(metadata.get("system")):
            return True
        if any(key in metadata for key in _AGENT_ONLY_METADATA_KEYS):
            return True
        source = metadata.get("source")
        if msg.channel == "discord" and source in _DISCORD_REVIEW_SOURCES:
            return True
        return False

    @staticmethod
    def _new_id() -> str:
        return uuid4().hex
