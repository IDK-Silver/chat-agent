from datetime import datetime, timezone

from chat_agent.cli import app as app_module


def test_codex_cache_bucket_supports_known_ttls():
    current_time = datetime(2026, 4, 11, 13, 27, tzinfo=timezone.utc)

    assert app_module._codex_cache_bucket("ephemeral", current_time=current_time) == "202604111305"
    assert app_module._codex_cache_bucket("1h", current_time=current_time) == "2026041113"
    assert app_module._codex_cache_bucket("24h", current_time=current_time) == "20260411"
    assert app_module._codex_cache_bucket("7d", current_time=current_time) is None


def test_codex_cache_key_provider_uses_session_namespace_and_bucket(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "tz_now",
        lambda: datetime(2026, 4, 11, 13, 27, tzinfo=timezone.utc),
    )
    provider = app_module._make_codex_cache_key_provider(
        session_id_getter=lambda: "session-1",
        namespace="brain",
        enabled=True,
        ttl="1h",
    )

    assert provider is not None
    assert provider() == "session-1:brain:2026041113"


def test_codex_cache_key_provider_disabled_returns_none():
    provider = app_module._make_codex_cache_key_provider(
        session_id_getter=lambda: "session-1",
        namespace="brain",
        enabled=False,
        ttl="1h",
    )

    assert provider is None
