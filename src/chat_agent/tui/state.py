"""UI state model for the Textual chat application."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .events import (
    AssistantTextEvent,
    CtxStatusEvent,
    DebugEvent,
    ErrorEvent,
    InboundMessageEvent,
    InterruptStateEvent,
    OutboundMessageEvent,
    ProcessingFinishedEvent,
    ProcessingStartedEvent,
    ResumeHistoryEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
    UiEvent,
    WarningEvent,
)


@dataclass(slots=True)
class UiLogEntry:
    """One logical row in the TUI log pane."""

    kind: str
    text: str


@dataclass(slots=True)
class UiState:
    """Serializable state backing the Textual UI widgets."""

    ctx_status: str = ""
    busy: bool = False
    interrupt_state: str = "idle"
    interrupt_message: str = ""
    log: list[UiLogEntry] = field(default_factory=list)
    pending_count: int = 0

    def _append_log(self, kind: str, text: str) -> None:
        """Append a log row and suppress immediate duplicates."""
        candidate = UiLogEntry(kind, text)
        if self.log:
            last = self.log[-1]
            if last.kind == candidate.kind and last.text == candidate.text:
                return
        self.log.append(candidate)

    @staticmethod
    def _ts(ts: datetime) -> str:
        """Format event timestamp using local time for display."""
        return ts.astimezone().strftime("%m/%d %H:%M:%S")

    def append_event(self, event: UiEvent) -> None:
        """Apply one UI event to local state."""
        match event:
            case CtxStatusEvent(text=text):
                self.ctx_status = text
            case InterruptStateEvent(phase=phase, message=message):
                self.interrupt_state = phase
                self.interrupt_message = message
            case ProcessingStartedEvent(channel=channel, sender=sender):
                self.busy = True
                source = f"{channel}/{sender}" if sender else channel
                self._append_log("processing", f"source={source}")
            case ProcessingFinishedEvent(interrupted=interrupted):
                self.busy = False
                if interrupted:
                    self._append_log("info", "Turn interrupted")
            case InboundMessageEvent(timestamp=ts, channel=channel, sender=sender, content=content):
                source = f"{channel}/{sender}" if sender else channel
                self._append_log(
                    "inbound",
                    f"{self._ts(ts)} source={source}\n{content}",
                )
            case AssistantTextEvent(content=content):
                self._append_log("assistant", content)
            case OutboundMessageEvent(timestamp=ts, channel=channel, recipient=recipient, content=content):
                target = f"{channel}/{recipient}" if recipient else channel
                self._append_log(
                    "outbound",
                    f"{self._ts(ts)} target={target}\n{content}",
                )
            case ToolCallEvent(name=name, summary=summary):
                self._append_log("tool_call", f"{name}\n{summary}".strip())
            case ToolResultEvent(name=name, summary=summary, failed=failed, warning=warning):
                level = "tool_error" if failed else ("tool_warn" if warning else "tool_result")
                self._append_log(level, f"{name}\n{summary}".strip())
            case ToolStreamEvent(line=line):
                self._append_log("tool_stream", line)
            case WarningEvent(message=message):
                self._append_log("warning", message)
            case ErrorEvent(message=message):
                self._append_log("error", message)
            case DebugEvent(label=label, message=message):
                self._append_log("debug", f"{label}\n{message}")
            case ResumeHistoryEvent(summary=summary):
                self._append_log("resume", summary)
            case _:
                self._append_log("info", str(event))
