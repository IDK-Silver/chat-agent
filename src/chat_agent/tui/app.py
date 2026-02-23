"""Textual application shell for the chat CLI (Phase 0/1 foundation)."""

from __future__ import annotations

import time
import threading
from collections.abc import Callable
from dataclasses import dataclass

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, RichLog, Static, TextArea

from .controller import TextualController
from .events import CtxStatusEvent, InterruptStateEvent, UiEvent
from .history_modal import HistoryModal
from .state import UiState


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
        title: str = "chat-cli",
    ) -> None:
        super().__init__()
        self.title = title
        self.sub_title = "Textual UI foundation"
        self.controller = controller
        self.state_model = UiState()
        self._ui: _UiRefs | None = None
        self._ctrl_c_ts = 0.0
        self._log_text_cache: list[str] = []
        self._status_text_cache = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="main"):
            yield RichLog(id="log", wrap=True, highlight=False, auto_scroll=True)
            yield Static("", id="status")
            yield TextArea(id="input")
        yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#log", RichLog)
        status = self.query_one("#status", Static)
        input_box = self.query_one("#input", TextArea)
        self._ui = _UiRefs(log=log, status=status, input=input_box)
        input_box.focus()
        self.set_interval(1.0, self._tick_ctx_refresh)
        for entry in self.state_model.log:
            line = f"[{entry.kind}] {entry.text}"
            self._log_text_cache.append(line)
            log.write(line)
        self._render_status()

    def post_ui_event(self, event: UiEvent) -> None:
        """Thread-safe entry point for worker threads to post a UI event."""
        if (
            getattr(self, "is_running", False)
            and getattr(self, "_thread_id", None) != threading.get_ident()
        ):
            self.call_from_thread(self._apply_ui_event, event)
            return
        self._apply_ui_event(event)

    def _tick_ctx_refresh(self) -> None:
        if self.controller is not None:
            self.controller.refresh_ctx_status()

    def _apply_ui_event(self, event: UiEvent) -> None:
        self.state_model.append_event(event)
        if isinstance(event, CtxStatusEvent | InterruptStateEvent):
            self._render_status()
            return
        if self._ui is None:
            return
        entry = self.state_model.log[-1] if self.state_model.log else None
        if entry is None:
            return
        line = f"[{entry.kind}] {entry.text}"
        self._log_text_cache.append(line)
        self._ui.log.write(line)
        self._render_status()

    def _render_status(self) -> None:
        if self._ui is None:
            return
        ctx = self.state_model.ctx_status or "ctx ?"
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
        line = f"[info] {text}"
        self._ui.log.write(line)
        self._log_text_cache.append(line)

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
