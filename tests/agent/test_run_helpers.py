"""Tests for agent runtime logging helpers."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from lincy.agent.run_helpers import _debug_print_responder_output
from lincy.llm.schema import LLMResponse


def test_cache_usage_logs_even_when_console_debug_disabled(caplog) -> None:
    console = MagicMock()
    console.debug = False
    response = LLMResponse(
        content="ok",
        tool_calls=[],
        prompt_tokens=100,
        usage_available=True,
        cache_read_tokens=90,
        cache_write_tokens=10,
    )

    with caplog.at_level(logging.INFO):
        _debug_print_responder_output(console, response, label="responder")

    assert any(
        record.getMessage() == "cache: read=90 prompt=100 rate=90% write=10"
        for record in caplog.records
    )
    console.print_debug.assert_not_called()
    console.print_debug_block.assert_not_called()
