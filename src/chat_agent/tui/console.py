"""Compatibility wrapper for the Textual CLI console name."""

from __future__ import annotations

from ..agent.ui_event_console import UiEventConsole


class TextualUiConsole(UiEventConsole):
    """Backward-compatible name used by CLI wiring and tests."""

