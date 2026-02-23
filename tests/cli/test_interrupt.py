"""Tests for ESC interrupt monitor helpers."""

from __future__ import annotations

import os
import signal

from chat_agent.cli.interrupt import EscInterruptMonitor


def test_send_interrupt_prefers_pthread_kill(monkeypatch):
    monitor = EscInterruptMonitor()
    monitor._main_thread_id = 123  # type: ignore[attr-defined]

    calls: list[tuple[str, int, int]] = []

    def fake_pthread_kill(tid: int, sig: int) -> None:
        calls.append(("pthread", tid, sig))

    def fake_os_kill(pid: int, sig: int) -> None:
        calls.append(("os", pid, sig))

    monkeypatch.setattr(signal, "pthread_kill", fake_pthread_kill, raising=False)
    monkeypatch.setattr(os, "kill", fake_os_kill)

    monitor._send_interrupt()

    assert calls == [("pthread", 123, signal.SIGINT)]


def test_send_interrupt_falls_back_to_os_kill(monkeypatch):
    monitor = EscInterruptMonitor()
    monitor._main_thread_id = None  # type: ignore[attr-defined]

    calls: list[tuple[int, int]] = []

    monkeypatch.setattr(signal, "pthread_kill", None, raising=False)
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append((pid, sig)))

    monitor._send_interrupt()

    assert calls == [(os.getpid(), signal.SIGINT)]
