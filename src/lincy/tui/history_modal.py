"""Textual modal for Ctrl+R history selection."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static


class HistoryModal(ModalScreen[int | None]):
    """A simple modal that lets the user select a previous input."""

    CSS = """
    HistoryModal {
        align: center middle;
    }
    #history-dialog {
        width: 80%;
        max-width: 100;
        height: 70%;
        border: round $accent;
        background: $surface;
        padding: 1;
    }
    #history-title {
        margin-bottom: 1;
    }
    #history-list {
        height: 1fr;
        border: round $surface-darken-1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, items: list[str]) -> None:
        super().__init__()
        self._items = items

    def compose(self) -> ComposeResult:
        items = [
            ListItem(Label(f"{idx}. {text}"))
            for idx, text in enumerate(self._items, start=1)
        ]
        with Container(id="history-dialog"):
            with Vertical():
                yield Static("History (Ctrl+R)", id="history-title")
                yield ListView(*items, id="history-list")
                yield Static("Enter: select  Esc: cancel", classes="dim")

    def on_mount(self) -> None:
        list_view = self.query_one("#history-list", ListView)
        list_view.focus()
        if self._items:
            list_view.index = 0

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        list_view = event.list_view
        if list_view.index is None:
            self.dismiss(None)
            return
        self.dismiss(int(list_view.index))
