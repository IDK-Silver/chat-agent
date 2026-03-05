"""Scheduler channel adapter: heartbeat and scheduled wake-up messages.

On startup, clears old system heartbeats from pending/. It can optionally
enqueue an immediate startup heartbeat. After each heartbeat turn completes,
AgentCore._process_inbound auto-creates the next one with a random delay.
"""

from __future__ import annotations

import logging
import random
import re
from datetime import timedelta
from typing import TYPE_CHECKING

from ..schema import InboundMessage, OutboundMessage
from ...timezone_utils import get_tz, localise as tz_localise, now as tz_now

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
    not_before=None,
    interval_spec: str = "2h-5h",
    is_startup: bool = False,
) -> InboundMessage:
    """Create a heartbeat InboundMessage."""
    if is_startup:
        content = _STARTUP_CONTENT
    else:
        heartbeat_time = tz_localise(not_before) if not_before else tz_now()
        time_str = heartbeat_time.strftime("%Y-%m-%d %H:%M")
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


_PRE_SLEEP_SYNC_CONTENT = (
    "[PRE-SLEEP SYNC]\n"
    "Memory sync before quiet hours dormancy."
)


def make_pre_sleep_sync_message(
    *,
    not_before,
) -> InboundMessage:
    """Create a pre-sleep sync InboundMessage (no ``recurring`` flag)."""
    return InboundMessage(
        channel="system",
        content=_PRE_SLEEP_SYNC_CONTENT,
        priority=5,
        sender="system",
        metadata={"system": True, "pre_sleep_sync": True},
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
        quiet_windows: list[tuple] | None = None,
    ) -> None:
        self._interval = interval
        self._enqueue_startup = enqueue_startup
        self._upgrade_message = upgrade_message
        self._quiet_windows = quiet_windows or []

    def start(self, agent: AgentCore) -> None:
        """Clear old heartbeats and seed the recurring heartbeat chain."""
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
            delay = random_delay(self._interval)
            next_time = self._apply_quiet_hours(tz_now() + delay)
            delayed_msg = make_heartbeat_message(
                not_before=next_time,
                interval_spec=self._interval,
            )
            agent.enqueue(delayed_msg)
            if self._upgrade_message:
                logger.info(
                    "Startup heartbeat disabled; seeded delayed heartbeat and skipped upgrade startup message"
                )
            else:
                logger.info("Startup heartbeat disabled; seeded delayed heartbeat")
            return

        # Enqueue startup heartbeat (with upgrade info if available).
        # If startup lands in quiet hours, defer it to quiet-end boundary.
        if self._upgrade_message:
            content = self._upgrade_message
        else:
            content = _STARTUP_CONTENT

        now = tz_now()
        startup_at = self._apply_quiet_hours(now)
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
            not_before=startup_at if startup_at > now else None,
        )
        agent.enqueue(startup_msg)
        if startup_at > now:
            logger.info("Startup heartbeat deferred to %s", startup_at.isoformat())
        else:
            logger.info("Startup heartbeat enqueued")

    def _apply_quiet_hours(self, dt):
        """Push *dt* past quiet hours if it falls within a blackout window."""
        if not self._quiet_windows:
            return dt
        from ...core.schema import is_in_quiet_hours, next_quiet_end

        tz = get_tz()
        if is_in_quiet_hours(dt, self._quiet_windows, tz):
            end = next_quiet_end(dt, self._quiet_windows, tz)
            logger.info("Heartbeat deferred past quiet hours to %s", end.astimezone(tz))
            return end
        return dt

    def send(self, message: OutboundMessage) -> None:
        """No-op: system channel does not send outbound messages."""

    def on_turn_start(self, channel: str) -> None:
        pass

    def on_turn_complete(self) -> None:
        pass

    def stop(self) -> None:
        pass
