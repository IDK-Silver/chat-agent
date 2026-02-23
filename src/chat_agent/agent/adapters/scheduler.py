"""Scheduler channel adapter: heartbeat and scheduled wake-up messages.

On startup, clears old system heartbeats from pending/. It can optionally
enqueue an immediate startup heartbeat. After each heartbeat turn completes,
AgentCore._process_inbound auto-creates the next one with a random delay.
"""

from __future__ import annotations

import logging
import random
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from ..schema import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from ..core import AgentCore

logger = logging.getLogger(__name__)

# Matches "2h-5h", "30m-90m", or mixed "1h-30m"
_INTERVAL_RE = re.compile(r"^(\d+)([hm])-(\d+)([hm])$")

_STARTUP_CONTENT = (
    "[STARTUP]\n"
    "You just woke up. Check your memory for anything important.\n"
    "Greet the user if appropriate, or stay silent."
)

_HEARTBEAT_TEMPLATE = (
    "[HEARTBEAT]\n"
    "Time: {time}\n\n"
    "You have woken up spontaneously.\n"
    "Check your memory for pending tasks, reminders, or anything\n"
    "you want to tell the user. If nothing to do, do nothing."
)


def _to_minutes(value: int, unit: str) -> int:
    """Convert a value with unit suffix to minutes."""
    return value * 60 if unit == "h" else value


def parse_interval(spec: str) -> tuple[int, int]:
    """Parse interval spec into (lo_minutes, hi_minutes).

    Accepts hours (h) or minutes (m) on each side independently:
    ``"2h-5h"``, ``"30m-90m"``, ``"1h-30m"`` are all valid.
    """
    m = _INTERVAL_RE.match(spec)
    if not m:
        raise ValueError(f"Invalid interval spec: {spec!r}")
    lo = _to_minutes(int(m.group(1)), m.group(2))
    hi = _to_minutes(int(m.group(3)), m.group(4))
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def random_delay(spec: str) -> timedelta:
    """Return a random timedelta within the interval spec."""
    lo, hi = parse_interval(spec)
    minutes = random.uniform(lo, hi)
    return timedelta(minutes=minutes)


def make_heartbeat_message(
    *,
    not_before: datetime | None = None,
    interval_spec: str = "2h-5h",
    is_startup: bool = False,
) -> InboundMessage:
    """Create a heartbeat InboundMessage."""
    if is_startup:
        content = _STARTUP_CONTENT
    else:
        time_str = (not_before or datetime.now(timezone.utc)).strftime(
            "%Y-%m-%d %H:%M"
        )
        content = _HEARTBEAT_TEMPLATE.format(time=time_str)

    return InboundMessage(
        channel="system",
        content=content,
        priority=5,
        sender="system",
        metadata={
            "system": True,
            "recurring": True,
            "recur_spec": interval_spec,
        },
        not_before=not_before,
    )


class SchedulerAdapter:
    """System channel adapter for heartbeat and scheduled actions.

    Thin adapter: ``start()`` optionally seeds the queue with a startup
    heartbeat. The recurring logic lives in ``AgentCore._process_inbound``.
    """

    channel_name = "system"
    priority = 5

    def __init__(
        self,
        *,
        interval: str = "2h-5h",
        enqueue_startup: bool = False,
        upgrade_message: str = "",
    ) -> None:
        self._interval = interval
        self._enqueue_startup = enqueue_startup
        self._upgrade_message = upgrade_message

    def start(self, agent: AgentCore) -> None:
        """Clear old heartbeats and optionally enqueue a startup heartbeat."""
        q = agent._queue
        if q is None:
            return

        # Clear stale system heartbeats from a previous run
        cleared = 0
        for filepath, msg in q.scan_pending(channel="system"):
            if msg.metadata.get("system"):
                q.remove_pending(filepath)
                cleared += 1
        if cleared:
            logger.info("Cleared %d old system heartbeat(s)", cleared)

        if not self._enqueue_startup:
            if self._upgrade_message:
                logger.info(
                    "Startup heartbeat disabled; upgrade startup message skipped"
                )
            else:
                logger.info("Startup heartbeat disabled")
            return

        # Enqueue immediate startup heartbeat (with upgrade info if available)
        if self._upgrade_message:
            content = self._upgrade_message
        else:
            content = _STARTUP_CONTENT

        startup_msg = InboundMessage(
            channel="system",
            content=content,
            priority=5,
            sender="system",
            metadata={
                "system": True,
                "recurring": True,
                "recur_spec": self._interval,
            },
        )
        agent.enqueue(startup_msg)
        logger.info("Startup heartbeat enqueued")

    def send(self, message: OutboundMessage) -> None:
        """No-op: system channel does not send outbound messages."""

    def on_turn_start(self, channel: str) -> None:
        pass

    def on_turn_complete(self) -> None:
        pass

    def stop(self) -> None:
        pass
