"""UI state model for the Textual chat application."""

from __future__ import annotations

from dataclasses import dataclass, field

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
                self.log.append(
                    UiLogEntry("processing", f"processing [{channel}] ({sender or 'unknown'})")
                )
            case ProcessingFinishedEvent(interrupted=interrupted):
                self.busy = False
                if interrupted:
                    self.log.append(UiLogEntry("info", "Turn interrupted"))
            case InboundMessageEvent(channel=channel, sender=sender, content=content):
                who = sender or channel
                self.log.append(UiLogEntry("inbound", f"{who}: {content}"))
            case AssistantTextEvent(content=content):
                self.log.append(UiLogEntry("assistant", content))
            case OutboundMessageEvent(channel=channel, recipient=recipient, content=content):
                target = recipient or channel
                self.log.append(UiLogEntry("outbound", f"{target}: {content}"))
            case ToolCallEvent(name=name, summary=summary):
                self.log.append(UiLogEntry("tool_call", f"{name}: {summary}".strip(": ")))
            case ToolResultEvent(name=name, summary=summary, failed=failed, warning=warning):
                level = "tool_error" if failed else ("tool_warn" if warning else "tool_result")
                self.log.append(UiLogEntry(level, f"{name}: {summary}".strip(": ")))
            case ToolStreamEvent(line=line):
                self.log.append(UiLogEntry("tool_stream", line))
            case WarningEvent(message=message):
                self.log.append(UiLogEntry("warning", message))
            case ErrorEvent(message=message):
                self.log.append(UiLogEntry("error", message))
            case DebugEvent(label=label, message=message):
                self.log.append(UiLogEntry("debug", f"{label}: {message}"))
            case ResumeHistoryEvent(summary=summary):
                self.log.append(UiLogEntry("resume", summary))
            case _:
                self.log.append(UiLogEntry("info", str(event)))
