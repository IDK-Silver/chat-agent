from datetime import datetime, timezone

import pytest

from chat_agent.timezone_utils import (
    configure,
    format_in_timezone,
    get_spec,
    get_tz,
    localise,
    now,
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


# ------------------------------------------------------------------
# Singleton API (configure / get_tz / now / localise)
# ------------------------------------------------------------------


def test_configure_and_get_tz():
    """configure() was already called by conftest; verify get_tz() works."""
    tz = get_tz()
    assert tz is not None
    assert get_spec() == "UTC+8"


def test_now_returns_aware_in_app_tz():
    dt = now()
    assert dt.tzinfo is not None
    assert dt.utcoffset() == get_tz().utcoffset(None)


def test_localise_converts_utc_to_app_tz():
    utc_dt = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    local = localise(utc_dt)
    assert local.strftime("%Y-%m-%d %H:%M") == "2026-03-01 16:00"
    assert local.utcoffset() == get_tz().utcoffset(None)


def test_localise_noop_for_same_tz():
    """localise() on an already-local datetime is a no-op."""
    tz = get_tz()
    local_dt = datetime(2026, 3, 1, 16, 0, tzinfo=tz)
    result = localise(local_dt)
    assert result == local_dt


def test_localise_treats_naive_as_utc():
    naive = datetime(2026, 3, 1, 8, 0)
    local = localise(naive)
    assert local.strftime("%Y-%m-%d %H:%M") == "2026-03-01 16:00"


def test_reconfigure_changes_timezone():
    """configure() can be called again (e.g. in tests) to change timezone."""
    configure("UTC+9")
    try:
        assert get_spec() == "UTC+9"
        utc_dt = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
        assert localise(utc_dt).strftime("%H:%M") == "17:00"
    finally:
        # Restore for other tests
        configure("UTC+8")
