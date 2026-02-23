from datetime import datetime, timezone

import pytest

from chat_agent.timezone_utils import (
    format_in_timezone,
    parse_timezone_spec,
    validate_timezone_spec,
)


def test_parse_timezone_spec_supports_utc_shorthand():
    tz = parse_timezone_spec("UTC+8")
    dt = datetime(2026, 3, 1, 14, 37, tzinfo=timezone.utc)
    assert dt.astimezone(tz).strftime("%Y-%m-%d %H:%M") == "2026-03-01 22:37"


def test_parse_timezone_spec_supports_utc_with_minutes():
    tz = parse_timezone_spec("UTC-05:30")
    dt = datetime(2026, 3, 1, 14, 37, tzinfo=timezone.utc)
    assert dt.astimezone(tz).strftime("%Y-%m-%d %H:%M") == "2026-03-01 09:07"


def test_parse_timezone_spec_supports_iana_name():
    tz = parse_timezone_spec("Asia/Taipei")
    dt = datetime(2026, 3, 1, 14, 37, tzinfo=timezone.utc)
    assert dt.astimezone(tz).strftime("%Y-%m-%d %H:%M") == "2026-03-01 22:37"


@pytest.mark.parametrize(
    "value",
    ["", "UTC+25", "UTC+8:99", "Taipei", "Invalid/Timezone"],
)
def test_parse_timezone_spec_rejects_invalid_values(value: str):
    with pytest.raises(ValueError):
        parse_timezone_spec(value)


def test_validate_timezone_spec_returns_original_value():
    assert validate_timezone_spec("UTC+08:00") == "UTC+08:00"


def test_format_in_timezone_uses_configured_timezone():
    dt = datetime(2026, 3, 1, 14, 37, tzinfo=timezone.utc)
    assert format_in_timezone(dt, "UTC+8", "%Y-%m-%d %H:%M") == "2026-03-01 22:37"
