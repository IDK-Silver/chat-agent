"""Compact Calendar/Reminders snapshots injected through agent_note."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..timezone_utils import localise as tz_localise, now as tz_now

if TYPE_CHECKING:
    from ..core.schema import AppleAppsContextSyncConfig
    from ..tools.builtin.macos_apps import MacOSAppBridge
    from .note_store import NoteStore

logger = logging.getLogger(__name__)

APPLE_APPS_CONTEXT_STATE_FILENAME = "apple_apps_context.json"

_CALENDAR_NEXT_EVENT_KEY = "calendar.next_event"
_CALENDAR_TODAY_SUMMARY_KEY = "calendar.today_summary"
_REMINDERS_NEXT_DUE_KEY = "reminders.next_due"
_REMINDERS_TODAY_FOCUS_KEY = "reminders.today_focus"


def ensure_apple_apps_context_state(state_dir: Path) -> Path:
    """Create the persisted refresh-state file if it does not exist yet."""
    path = state_dir / APPLE_APPS_CONTEXT_STATE_FILENAME
    if path.exists():
        return path
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"last_refresh_at": None}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


class AppleAppsContextSync:
    """Refresh compact Calendar/Reminders snapshots into NoteStore."""

    def __init__(
        self,
        *,
        bridge: MacOSAppBridge,
        note_store: NoteStore,
        state_dir: Path,
        sync_config: AppleAppsContextSyncConfig,
    ) -> None:
        self._bridge = bridge
        self._note_store = note_store
        self._config = sync_config
        self._state_path = ensure_apple_apps_context_state(state_dir)
        self._last_refresh_at = self._load_last_refresh_at()

    def maybe_refresh(
        self,
        *,
        reason: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """Refresh note snapshots when stale or when force=True."""
        if not self._config.enabled:
            return {"ok": True, "refreshed": False, "reason": "disabled"}
        now = tz_now()
        if not force and not self._is_stale(now):
            return {"ok": True, "refreshed": False, "reason": "cooldown"}

        errors: list[str] = []
        self._refresh_calendar_notes(now, errors)
        self._refresh_reminder_notes(now, errors)
        self._last_refresh_at = now
        self._save_last_refresh_at(now)
        if errors:
            logger.warning(
                "Apple apps context refresh completed with %d error(s): %s",
                len(errors),
                "; ".join(errors),
            )
        else:
            logger.info("Apple apps context refreshed (%s)", reason)
        return {"ok": not errors, "refreshed": True, "errors": errors}

    def _is_stale(self, now: datetime) -> bool:
        if self._last_refresh_at is None:
            return True
        cooldown = timedelta(seconds=self._config.cooldown_seconds)
        if cooldown <= timedelta(0):
            return True
        return now - self._last_refresh_at >= cooldown

    def _refresh_calendar_notes(self, now: datetime, errors: list[str]) -> None:
        horizon = now + timedelta(hours=self._config.calendar_window_hours)
        upcoming = self._bridge.calendar_search(
            calendar=None,
            calendars=None,
            query=None,
            start=now.isoformat(),
            end=horizon.isoformat(),
            all_day=None,
            sort_by="start_asc",
            limit=self._config.calendar_max_events,
        )
        if not upcoming.get("ok"):
            errors.append(f"calendar: {upcoming.get('error', 'unknown error')}")
            return

        next_event = None
        results = upcoming.get("results", [])
        if results:
            next_event = results[0]
        self._note_store.upsert(
            key=_CALENDAR_NEXT_EVENT_KEY,
            value=_build_next_event_value(next_event, self._config.calendar_window_hours),
            description=(
                "System-managed snapshot from macOS Calendar. "
                "Use calendar_tool for more detail or any write."
            ),
            source_app="calendar",
            source_id=next_event.get("uid") if isinstance(next_event, dict) else None,
            source_label="next_event",
        )

        today = self._bridge.calendar_search(
            calendar=None,
            calendars=None,
            query=None,
            start=now.isoformat(),
            end=_end_of_day(now).isoformat(),
            all_day=None,
            sort_by="start_asc",
            limit=self._config.calendar_max_events,
        )
        if not today.get("ok"):
            errors.append(f"calendar(today): {today.get('error', 'unknown error')}")
            return
        self._note_store.upsert(
            key=_CALENDAR_TODAY_SUMMARY_KEY,
            value=_build_today_events_value(today.get("results", [])),
            description=(
                "System-managed summary of today's remaining macOS Calendar events."
            ),
            source_app="calendar",
            source_label="today_summary",
        )

    def _refresh_reminder_notes(self, now: datetime, errors: list[str]) -> None:
        next_due_result = self._bridge.reminders_search(
            list_id=None,
            list_name=None,
            list_path=None,
            query=None,
            due_start=None,
            due_end=(now + timedelta(days=self._config.reminders_window_days)).isoformat(),
            completed=False,
            flagged=None,
            priority_min=None,
            priority_max=None,
            sort_by="due_asc",
            limit=self._config.reminders_max_items,
        )
        if not next_due_result.get("ok"):
            errors.append(
                f"reminders(next_due): {next_due_result.get('error', 'unknown error')}"
            )
            return
        next_due = _first_due_item(next_due_result.get("results", []))

        today_focus_result = self._bridge.reminders_search(
            list_id=None,
            list_name=None,
            list_path=None,
            query=None,
            due_start=None,
            due_end=_end_of_day(now).isoformat(),
            completed=False,
            flagged=None,
            priority_min=None,
            priority_max=None,
            sort_by="due_asc",
            limit=self._config.reminders_max_items,
        )
        if not today_focus_result.get("ok"):
            errors.append(
                f"reminders(today_focus): {today_focus_result.get('error', 'unknown error')}"
            )
            return

        self._note_store.upsert(
            key=_REMINDERS_NEXT_DUE_KEY,
            value=_build_next_due_value(
                next_due,
                self._config.reminders_window_days,
                now=now,
            ),
            description=(
                "System-managed snapshot of the next due macOS reminder. "
                "Use reminders_tool for more detail or writes."
            ),
            source_app="reminders",
            source_id=next_due.get("id") if isinstance(next_due, dict) else None,
            source_label="next_due",
        )
        self._note_store.upsert(
            key=_REMINDERS_TODAY_FOCUS_KEY,
            value=_build_today_focus_value(today_focus_result.get("results", []), now),
            description=(
                "System-managed snapshot of overdue or due-today macOS reminders."
            ),
            source_app="reminders",
            source_label="today_focus",
        )

    def _load_last_refresh_at(self) -> datetime | None:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning(
                "Failed to load apple apps context state from %s",
                self._state_path,
                exc_info=True,
            )
            return None
        value = raw.get("last_refresh_at")
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _save_last_refresh_at(self, refreshed_at: datetime) -> None:
        tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                {"last_refresh_at": refreshed_at.isoformat()},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        tmp.replace(self._state_path)


def _end_of_day(now: datetime) -> datetime:
    return now.replace(hour=23, minute=59, second=59, microsecond=0)


def _first_due_item(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in rows:
        if row.get("due"):
            return row
    return None


def _build_next_event_value(
    row: dict[str, Any] | None,
    window_hours: int,
) -> str:
    if row is None:
        return f"No calendar events in next {window_hours}h."
    when = _format_event_range(row)
    parts = [when, row.get("title") or "(untitled event)"]
    calendar = row.get("calendar")
    if calendar:
        parts.append(f"[{calendar}]")
    location = row.get("location")
    if location:
        parts.append(f"@ {location}")
    return _join_main_and_suffix(parts)


def _build_today_events_value(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No remaining calendar events today."
    items = [_format_today_event_item(row) for row in rows]
    return f"Today: {'; '.join(items)}"


def _build_next_due_value(
    row: dict[str, Any] | None,
    window_days: int,
    *,
    now: datetime,
) -> str:
    if row is None:
        return f"No due reminders in next {window_days}d."
    due = _format_due_label(row.get("due"), now=now)
    title = row.get("title") or "(untitled reminder)"
    list_path = row.get("list_path")
    parts = [due, title]
    if list_path:
        parts.append(f"[{list_path}]")
    return _join_main_and_suffix(parts)


def _build_today_focus_value(rows: list[dict[str, Any]], now: datetime) -> str:
    due_rows = [row for row in rows if row.get("due")]
    if not due_rows:
        return "No overdue or due-today reminders."
    items = [_format_today_reminder_item(row, now=now) for row in due_rows]
    return f"Reminder focus: {'; '.join(items)}"


def _format_event_range(row: dict[str, Any]) -> str:
    start_value = row.get("start")
    end_value = row.get("end")
    if not start_value:
        return "time unknown"
    start = tz_localise(datetime.fromisoformat(start_value))
    if row.get("all_day"):
        return start.strftime("%Y-%m-%d all day")
    if not end_value:
        return start.strftime("%Y-%m-%d %H:%M")
    end = tz_localise(datetime.fromisoformat(end_value))
    if start.date() == end.date():
        return f"{start.strftime('%Y-%m-%d %H:%M')}-{end.strftime('%H:%M')}"
    return f"{start.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%Y-%m-%d %H:%M')}"


def _format_today_event_item(row: dict[str, Any]) -> str:
    start_value = row.get("start")
    if not start_value:
        label = "time unknown"
    else:
        start = tz_localise(datetime.fromisoformat(start_value))
        if row.get("all_day"):
            label = "all day"
        else:
            end_value = row.get("end")
            if end_value:
                end = tz_localise(datetime.fromisoformat(end_value))
                label = (
                    f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
                    if start.date() == end.date()
                    else start.strftime("%H:%M")
                )
            else:
                label = start.strftime("%H:%M")
    title = row.get("title") or "(untitled event)"
    calendar = row.get("calendar")
    if calendar:
        return f"{label} {title} [{calendar}]"
    return f"{label} {title}"


def _format_due_label(value: str | None, *, now: datetime) -> str:
    if not value:
        return "no due date"
    due = tz_localise(datetime.fromisoformat(value))
    if due < now:
        return f"overdue since {due.strftime('%Y-%m-%d %H:%M')}"
    if due.date() == now.date():
        return f"today {due.strftime('%H:%M')}"
    return due.strftime("%Y-%m-%d %H:%M")


def _format_today_reminder_item(row: dict[str, Any], *, now: datetime) -> str:
    title = row.get("title") or "(untitled reminder)"
    due = _format_due_label(row.get("due"), now=now)
    list_path = row.get("list_path")
    if list_path:
        return f"{due} {title} [{list_path}]"
    return f"{due} {title}"


def _join_main_and_suffix(parts: list[str]) -> str:
    if len(parts) <= 2:
        return " | ".join(parts)
    return " | ".join(parts[:2]) + " " + " ".join(parts[2:])
