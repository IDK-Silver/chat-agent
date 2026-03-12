from datetime import datetime, timedelta, timezone

from chat_agent.turn_timing import build_turn_timing_metadata


def test_scheduled_turn_near_on_time_does_not_mark_stale():
    event_ts = datetime(2026, 3, 12, 7, 50, tzinfo=timezone.utc)
    turn_metadata = build_turn_timing_metadata(
        channel="system",
        metadata={"scheduled_reason": "wake"},
        event_timestamp=event_ts,
        processing_started_at=event_ts + timedelta(minutes=2),
    )

    assert "turn_processing_delay_reason" not in turn_metadata
    assert "turn_processing_stale" not in turn_metadata


def test_failed_retry_keeps_delay_reason_even_before_stale_threshold():
    event_ts = datetime(2026, 3, 12, 0, 27, tzinfo=timezone.utc)
    turn_metadata = build_turn_timing_metadata(
        channel="discord",
        metadata={"turn_failure_requeue_count": 1},
        event_timestamp=event_ts,
        processing_started_at=event_ts + timedelta(minutes=1),
    )

    assert turn_metadata["turn_processing_delay_reason"] == "failed_retry"
