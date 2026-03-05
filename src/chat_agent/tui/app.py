"""Textual application shell for the chat CLI (Phase 0/1 foundation)."""

from __future__ import annotations

import time
import threading
from collections.abc import Callable
from dataclasses import dataclass

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, RichLog, Static, TextArea

from .controller import TextualController
from .events import CtxStatusEvent, InterruptStateEvent, UiEvent
from .history_modal import HistoryModal
from .state import UiLogEntry, UiState
from .sink import QueueUiSink
from ..timezone_utils import format_in_timezone


_DOUBLE_CTRL_C_THRESHOLD = 0.4


@dataclass(slots=True)
class _UiRefs:
    log: RichLog
    status: Static
    input: TextArea


class ChatTextualApp(App[None]):
    """Single-renderer Textual shell for chat CLI UI events."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #main {
        height: 1fr;
    }
    #log {
        height: 1fr;
        border: round $surface;
        margin: 0 1;
    }
    #status {
        height: auto;
        min-height: 1;
        max-height: 3;
        overflow-y: hidden;
        border: round $surface;
        margin: 0 1;
        padding: 0 1;
    }
    #input {
        height: 6;
        min-height: 4;
        border: round $accent;
        margin: 0 1 1 1;
    }
    """

    BINDINGS = [
        Binding("escape", "interrupt", "Interrupt"),
        Binding("ctrl+r", "history", "History"),
        Binding("ctrl+c", "ctrl_c", "Clear / Exit"),
        Binding("ctrl+j", "insert_newline", "Newline", show=False),
        Binding("ctrl+s", "submit_input", "Send"),
    ]

    def __init__(
        self,
        *,
        controller: TextualController | None = None,
        event_sink: QueueUiSink | None = None,
        timezone: str | None = None,
        title: str = "chat-cli",
    ) -> None:
        super().__init__()
        self.title = title
        self.sub_title = "Textual UI foundation"
        self.controller = controller
        self._event_sink = event_sink
        self._timezone = timezone
        self.state_model = UiState(timezone=timezone)
        self._ui: _UiRefs | None = None
        self._ctrl_c_ts = 0.0
        self._log_text_cache: list[str] = []
        self._status_text_cache = ""
        self._log_follow_tail = True
        self._log_last_scroll_y: float | None = None
        self._log_last_max_scroll_y: float | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="main"):
            yield RichLog(id="log", wrap=True, highlight=False, auto_scroll=False)
            yield Static("", id="status")
            yield TextArea(id="input")
        yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#log", RichLog)
        status = self.query_one("#status", Static)
        input_box = self.query_one("#input", TextArea)
        self._ui = _UiRefs(log=log, status=status, input=input_box)
        self._sync_log_follow_tail()
        self.watch(log, "scroll_y", self._on_log_scroll_y_changed, init=False)
        self.watch(log, "max_scroll_y", self._on_log_max_scroll_y_changed, init=False)
        input_box.focus()
        self.set_interval(1.0, self._tick_ctx_refresh)
        self.set_interval(0.25, self._drain_queued_events)
        for entry in self.state_model.log:
            self._write_log_entry(entry)
        self._render_status()

    def on_resize(self, event: events.Resize) -> None:
        """Re-render widgets when terminal size changes."""
        # Force full layout recalculation so the screen fills the new
        # terminal dimensions (needed under tmux/SSH where Textual's
        # default resize propagation can leave stale layout).
        self.screen.refresh(layout=True)
        self._render_status()
        if self._log_follow_tail and self._ui is not None:
            self._ui.log.scroll_end(animate=False)

    def post_ui_event(self, event: UiEvent) -> None:
        """Thread-safe entry point for worker threads to post a UI event."""
        if getattr(self, "_thread_id", None) == threading.get_ident():
            self._apply_ui_event(event)
            return
        loop = getattr(self, "_loop", None)
        if loop is not None:
            loop.call_soon_threadsafe(self._apply_ui_event, event)
            return
        # Fallback: apply directly (pre-startup).
        self._apply_ui_event(event)

    def wake_ui_event_drain(self, _event: UiEvent) -> None:
        """Wake the UI thread to drain queued events from ``QueueUiSink`` in FIFO order.

        For same-thread calls (e.g. ctx_refresh tick), drain immediately.
        For cross-thread calls (e.g. agent processing), schedule a
        non-blocking drain via the asyncio event loop.  We avoid
        ``call_from_thread`` because Textual 8.x made it blocking
        (waits for result), which stalls the agent thread and can
        prevent events from reaching the UI.
        """
        if self._event_sink is None:
            self.post_ui_event(_event)
            return
        # Same thread: drain synchronously (fast path).
        if getattr(self, "_thread_id", None) == threading.get_ident():
            self._drain_queued_events()
            return
        # Cross-thread: non-blocking wake-up via asyncio loop.
        loop = getattr(self, "_loop", None)
        if loop is not None:
            loop.call_soon_threadsafe(self._drain_queued_events)

    def drain_ui_events(self) -> None:
        """Drain queued UI events (used during startup before the UI loop runs)."""
        self._drain_queued_events()

    def _drain_queued_events(self) -> None:
        if self._event_sink is None:
            return
        for event in self._event_sink.drain():
            self._apply_ui_event(event)

    def _tick_ctx_refresh(self) -> None:
        if self.controller is not None:
            self.controller.refresh_ctx_status()

    def _apply_ui_event(self, event: UiEvent) -> None:
        appended = self.state_model.append_event(event)
        if isinstance(event, CtxStatusEvent | InterruptStateEvent):
            self._render_status()
            return
        if not appended:
            self._render_status()
            return
        if self._ui is None:
            return
        entry = self.state_model.log[-1] if self.state_model.log else None
        if entry is None:
            return
        self._write_log_entry(entry)
        self._render_status()

    @staticmethod
    def _kind_label(kind: str) -> str:
        labels = {
            "inbound": "IN",
            "processing": "TURN",
            "assistant": "AGENT",
            "tool_call": "TOOL>",
            "tool_result": "TOOL<",
            "tool_warn": "TOOL!",
            "tool_error": "TOOLX",
            "tool_stream": "STREAM",
            "warning": "WARN",
            "error": "ERR",
            "debug": "DBG",
            "resume": "SYS",
            "info": "INFO",
        }
        return labels.get(kind, kind.upper())

    @staticmethod
    def _kind_style(kind: str) -> str:
        styles = {
            "inbound": "bold cyan",
            "processing": "bold yellow",
            "assistant": "bold white",
            "tool_call": "bold blue",
            "tool_result": "green",
            "tool_warn": "yellow",
            "tool_error": "bold red",
            "tool_stream": "dim cyan",
            "warning": "yellow",
            "error": "bold red",
            "debug": "dim",
            "resume": "dim cyan",
            "info": "dim white",
        }
        return styles.get(kind, "white")

    def _write_log_entry(self, entry: UiLogEntry) -> None:
        """Render one logical entry with timestamps, labels, and indentation."""
        if self._ui is None:
            return
        kind = entry.kind
        text = entry.text
        lines = text.splitlines() or [""]
        if entry.timestamp is None:
            ts = "--:--:--"
        elif self._timezone:
            ts = format_in_timezone(entry.timestamp, self._timezone, "%H:%M:%S")
        else:
            ts = entry.timestamp.astimezone().strftime("%H:%M:%S")
        label = self._kind_label(kind).ljust(6)
        label_style = self._kind_style(kind)

        render = Text()
        render.append(ts, style="dim")
        render.append(" ")
        render.append(label, style=label_style)
        render.append(" ")
        render.append(lines[0], style="white")

        cont_prefix = " " * (len(ts) + 1) + " " * len(label) + " "
        for line in lines[1:]:
            render.append("\n")
            render.append(cont_prefix, style="dim")
            render.append(line, style="white")

        # Keep plain-text cache for tests/introspection.
        cache_prefix = f"[{kind}] "
        cache_block = cache_prefix + lines[0]
        if len(lines) > 1:
            indent = " " * len(cache_prefix)
            cache_block += "".join(f"\n{indent}{line}" for line in lines[1:])
        self._log_text_cache.append(cache_block)
        self._ui.log.write(render, scroll_end=self._should_follow_log_tail())

    def _should_follow_log_tail(self) -> bool:
        """Follow tail only when the user is attached to the live end."""
        self._sync_log_follow_tail()
        return self._log_follow_tail

    @staticmethod
    def _is_near_log_tail(scroll_y: float, max_scroll_y: float) -> bool:
        return (max_scroll_y - scroll_y) <= 1

    def _sync_log_follow_tail(self) -> None:
        if self._ui is None:
            self._log_follow_tail = True
            return
        log = self._ui.log
        scroll_y = float(log.scroll_y)
        max_scroll_y = float(log.max_scroll_y)
        if self._log_last_scroll_y is None or self._log_last_max_scroll_y is None:
            self._log_follow_tail = self._is_near_log_tail(scroll_y, max_scroll_y)
        else:
            was_at_tail = self._is_near_log_tail(
                self._log_last_scroll_y, self._log_last_max_scroll_y
            )
            is_at_tail = self._is_near_log_tail(scroll_y, max_scroll_y)
            max_changed = max_scroll_y != self._log_last_max_scroll_y
            scroll_changed = abs(scroll_y - self._log_last_scroll_y) > 0.1
            if was_at_tail and not is_at_tail and max_changed and not scroll_changed:
                # Preserve tail-follow across resize/reflow when scroll position
                # is temporarily stale but user did not manually scroll away.
                self._log_follow_tail = True
            else:
                self._log_follow_tail = is_at_tail
        self._log_last_scroll_y = scroll_y
        self._log_last_max_scroll_y = max_scroll_y

    def _on_log_scroll_y_changed(self, _old: float, _new: float) -> None:
        self._sync_log_follow_tail()

    def _on_log_max_scroll_y_changed(self, _old: float, _new: float) -> None:
        self._sync_log_follow_tail()

    def _render_status(self) -> None:
        if self._ui is None:
            return
        ctx = self.state_model.ctx_status or "tok ?"
        busy = "busy" if self.state_model.busy else "idle"
        intr = self.state_model.interrupt_state
        intr_msg = self.state_model.interrupt_message
        status = f"{ctx} | turn={busy} | interrupt={intr}"
        if intr_msg:
            status += f" | {intr_msg}"
        self._status_text_cache = status
        self._ui.status.update(status)

    def _current_input_text(self) -> str:
        if self._ui is None:
            return ""
        return self._ui.input.text

    def _clear_input(self) -> None:
        if self._ui is None:
            return
        self._ui.input.clear()

    def _set_input_text(self, text: str) -> None:
        if self._ui is None:
            return
        self._ui.input.clear()
        if text:
            self._ui.input.insert(text)

    def on_key(self, event) -> None:
        if event.key != "enter":
            return
        if self._ui is None or self.focused is not self._ui.input:
            return
        text = self._current_input_text()
        if not text or "\n" not in text:
            event.prevent_default()
            event.stop()
            self.action_submit_input()

    def action_submit_input(self) -> None:
        text = self._current_input_text()
        if self.controller is None:
            return
        should_exit = self.controller.submit_input(text)
        self._clear_input()
        if should_exit:
            self.exit()

    def action_insert_newline(self) -> None:
        if self._ui is None:
            return
        self._ui.input.insert("\n")

    def action_interrupt(self) -> None:
        if self.controller is not None:
            self.controller.request_interrupt()

    def action_history(self) -> None:
        if self.controller is None:
            return
        options = self.controller.get_history_options()
        if not options:
            prefill = self.controller.request_history()
            if prefill:
                self._set_input_text(prefill)
                return
            self._append_info_line("No history item selected.")
            return
        self.push_screen(HistoryModal(options), self._on_history_modal_closed)

    def _on_history_modal_closed(self, selected_index: int | None) -> None:
        if self.controller is None:
            return
        if selected_index is None:
            return
        prefill = self.controller.select_history(selected_index)
        if prefill:
            self._set_input_text(prefill)
            return
        self._append_info_line("No history item selected.")

    def _append_info_line(self, text: str) -> None:
        if self._ui is None:
            return
        self._write_log_entry(UiLogEntry(kind="info", text=text))

    def action_ctrl_c(self) -> None:
        now = time.monotonic()
        if self._ctrl_c_ts and (now - self._ctrl_c_ts) < _DOUBLE_CTRL_C_THRESHOLD:
            if self.controller is not None:
                self.controller.request_exit()
            self.exit()
            return
        self._ctrl_c_ts = now
        self._clear_input()

    # Test helpers
    @property
    def log_lines(self) -> list[str]:
        return list(self._log_text_cache)

    @property
    def status_text(self) -> str:
        return self._status_text_cache
