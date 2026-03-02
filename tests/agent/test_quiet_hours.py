"""Tests for heartbeat quiet hours."""

from datetime import datetime, time, timezone, timedelta

import pytest

from chat_agent.core.schema import (
    HeartbeatConfig,
    _parse_quiet_window,
    _time_in_window,
    is_in_quiet_hours,
    next_quiet_end,
)
from chat_agent.timezone_utils import parse_timezone_spec


# ------------------------------------------------------------------
# Parsing
# ------------------------------------------------------------------


class TestParseQuietWindow:
    def test_basic(self):
        assert _parse_quiet_window("00:00-06:00") == (time(0, 0), time(6, 0))

    def test_cross_midnight(self):
        assert _parse_quiet_window("23:00-07:00") == (time(23, 0), time(7, 0))

    def test_single_digit_hour(self):
        assert _parse_quiet_window("1:00-6:30") == (time(1, 0), time(6, 30))

    def test_whitespace_stripped(self):
        assert _parse_quiet_window("  00:00-06:00  ") == (time(0, 0), time(6, 0))

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="invalid quiet_hours format"):
            _parse_quiet_window("0-6")

    def test_invalid_time(self):
        with pytest.raises(ValueError):
            _parse_quiet_window("25:00-06:00")

    def test_zero_duration(self):
        with pytest.raises(ValueError, match="zero duration"):
            _parse_quiet_window("06:00-06:00")


class TestHeartbeatConfigQuietHours:
    def test_valid(self):
        cfg = HeartbeatConfig(quiet_hours=["00:00-06:00", "14:00-14:30"])
        assert len(cfg.parsed_quiet_windows()) == 2

    def test_empty(self):
        cfg = HeartbeatConfig(quiet_hours=[])
        assert cfg.parsed_quiet_windows() == []

    def test_default_empty(self):
        cfg = HeartbeatConfig()
        assert cfg.quiet_hours == []

    def test_invalid_rejects(self):
        with pytest.raises(Exception):
            HeartbeatConfig(quiet_hours=["bad"])


# ------------------------------------------------------------------
# Time-in-window
# ------------------------------------------------------------------


class TestTimeInWindow:
    def test_normal_window_inside(self):
        assert _time_in_window(time(3, 0), time(0, 0), time(6, 0)) is True

    def test_normal_window_outside(self):
        assert _time_in_window(time(7, 0), time(0, 0), time(6, 0)) is False

    def test_normal_window_at_start(self):
        assert _time_in_window(time(0, 0), time(0, 0), time(6, 0)) is True

    def test_normal_window_at_end(self):
        # End is exclusive
        assert _time_in_window(time(6, 0), time(0, 0), time(6, 0)) is False

    def test_cross_midnight_before(self):
        assert _time_in_window(time(23, 30), time(23, 0), time(7, 0)) is True

    def test_cross_midnight_after(self):
        assert _time_in_window(time(3, 0), time(23, 0), time(7, 0)) is True

    def test_cross_midnight_outside(self):
        assert _time_in_window(time(12, 0), time(23, 0), time(7, 0)) is False


# ------------------------------------------------------------------
# is_in_quiet_hours (with timezone)
# ------------------------------------------------------------------


_UTC8 = parse_timezone_spec("UTC+8")


class TestIsInQuietHours:
    def _windows(self, *specs):
        return [_parse_quiet_window(s) for s in specs]

    def test_in_window(self):
        # 03:00 UTC+8 = 19:00 UTC previous day
        dt = datetime(2026, 3, 1, 19, 0, tzinfo=timezone.utc)
        assert is_in_quiet_hours(dt, self._windows("00:00-06:00"), _UTC8) is True

    def test_outside_window(self):
        # 10:00 UTC+8 = 02:00 UTC
        dt = datetime(2026, 3, 2, 2, 0, tzinfo=timezone.utc)
        assert is_in_quiet_hours(dt, self._windows("00:00-06:00"), _UTC8) is False

    def test_multiple_windows(self):
        windows = self._windows("00:00-06:00", "14:00-14:30")
        # 14:15 UTC+8
        dt = datetime(2026, 3, 2, 6, 15, tzinfo=timezone.utc)
        assert is_in_quiet_hours(dt, windows, _UTC8) is True

    def test_empty_windows(self):
        dt = datetime(2026, 3, 2, 3, 0, tzinfo=timezone.utc)
        assert is_in_quiet_hours(dt, [], _UTC8) is False

    def test_cross_midnight_window(self):
        windows = self._windows("23:00-07:00")
        # 01:00 UTC+8
        dt = datetime(2026, 3, 1, 17, 0, tzinfo=timezone.utc)
        assert is_in_quiet_hours(dt, windows, _UTC8) is True
        # 12:00 UTC+8
        dt2 = datetime(2026, 3, 1, 4, 0, tzinfo=timezone.utc)
        assert is_in_quiet_hours(dt2, windows, _UTC8) is False


# ------------------------------------------------------------------
# next_quiet_end
# ------------------------------------------------------------------


class TestNextQuietEnd:
    def _windows(self, *specs):
        return [_parse_quiet_window(s) for s in specs]

    def test_basic(self):
        # 03:00 UTC+8 in 00:00-06:00 -> end at 06:00 UTC+8
        dt = datetime(2026, 3, 1, 19, 0, tzinfo=timezone.utc)
        end = next_quiet_end(dt, self._windows("00:00-06:00"), _UTC8)
        local_end = end.astimezone(_UTC8)
        assert local_end.hour == 6
        assert local_end.minute == 0

    def test_cross_midnight_before_midnight(self):
        # 23:30 UTC+8 in 23:00-07:00 -> end at 07:00 next day
        dt = datetime(2026, 3, 1, 15, 30, tzinfo=timezone.utc)
        end = next_quiet_end(dt, self._windows("23:00-07:00"), _UTC8)
        local_end = end.astimezone(_UTC8)
        assert local_end.day == 2
        assert local_end.hour == 7

    def test_cross_midnight_after_midnight(self):
        # 01:00 UTC+8 in 23:00-07:00 -> end at 07:00 same day
        dt = datetime(2026, 3, 1, 17, 0, tzinfo=timezone.utc)
        end = next_quiet_end(dt, self._windows("23:00-07:00"), _UTC8)
        local_end = end.astimezone(_UTC8)
        assert local_end.day == 2
        assert local_end.hour == 7

    def test_picks_earliest_end(self):
        # 14:15 UTC+8, windows: 00:00-06:00 (not active), 14:00-14:30 (active)
        dt = datetime(2026, 3, 2, 6, 15, tzinfo=timezone.utc)
        end = next_quiet_end(dt, self._windows("00:00-06:00", "14:00-14:30"), _UTC8)
        local_end = end.astimezone(_UTC8)
        assert local_end.hour == 14
        assert local_end.minute == 30

    def test_not_in_any_window_returns_dt(self):
        dt = datetime(2026, 3, 2, 2, 0, tzinfo=timezone.utc)  # 10:00 UTC+8
        end = next_quiet_end(dt, self._windows("00:00-06:00"), _UTC8)
        assert end == dt


# ------------------------------------------------------------------
# SchedulerAdapter quiet hours integration
# ------------------------------------------------------------------


class TestSchedulerAdapterQuietHours:
    def test_delayed_seed_deferred_past_quiet(self):
        """When initial heartbeat falls in quiet hours, it should be deferred."""
        from unittest.mock import MagicMock, patch
        from chat_agent.agent.adapters.scheduler import SchedulerAdapter

        adapter = SchedulerAdapter(
            interval="30m-30m",  # Fixed delay for predictability
            enqueue_startup=False,
            timezone="UTC+8",
            quiet_windows=[_parse_quiet_window("00:00-06:00")],
        )

        agent = MagicMock()
        agent._queue = MagicMock()
        agent._queue.scan_pending.return_value = []

        # Freeze time to 05:00 UTC+8 (= 21:00 UTC prev day)
        # 30m delay -> 05:30 UTC+8, still in quiet -> should defer to 06:00
        frozen = datetime(2026, 3, 1, 21, 0, tzinfo=timezone.utc)
        with patch("chat_agent.agent.adapters.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = frozen
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            adapter.start(agent)

        # Check the enqueued message
        agent.enqueue.assert_called_once()
        msg = agent.enqueue.call_args[0][0]
        local_not_before = msg.not_before.astimezone(_UTC8)
        assert local_not_before.hour == 6
        assert local_not_before.minute == 0

    def test_startup_heartbeat_deferred_past_quiet(self):
        """Startup heartbeat should also respect quiet hours."""
        from unittest.mock import MagicMock, patch
        from chat_agent.agent.adapters.scheduler import SchedulerAdapter

        adapter = SchedulerAdapter(
            interval="30m-30m",
            enqueue_startup=True,
            timezone="UTC+8",
            quiet_windows=[_parse_quiet_window("00:00-06:00")],
        )

        agent = MagicMock()
        agent._queue = MagicMock()
        agent._queue.scan_pending.return_value = []

        # Freeze time to 05:00 UTC+8 (= 21:00 UTC prev day).
        # Startup should be deferred to 06:00 instead of immediate enqueue.
        frozen = datetime(2026, 3, 1, 21, 0, tzinfo=timezone.utc)
        with patch("chat_agent.agent.adapters.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = frozen
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            adapter.start(agent)

        agent.enqueue.assert_called_once()
        msg = agent.enqueue.call_args[0][0]
        assert msg.not_before is not None
        local_not_before = msg.not_before.astimezone(_UTC8)
        assert local_not_before.hour == 6
        assert local_not_before.minute == 0
