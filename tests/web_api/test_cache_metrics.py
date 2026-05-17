from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from chat_agent.session.schema import SessionMetadata
from chat_agent.llm.schema import ContentPart, Message
from chat_agent.session.debug_schema import SessionLLMRequestRecord
from chat_web_api.cache import MetricsCache, RequestMetrics, ResponseMetrics
from chat_web_api.session_reader import SessionFiles


def _meta(session_id: str) -> SessionMetadata:
    now = datetime(2026, 4, 11, 12, 0, tzinfo=UTC)
    return SessionMetadata(
        session_id=session_id,
        user_id="u1",
        display_name="User",
        created_at=now,
        updated_at=now,
        status="active",
    )


def _request(
    request_id: str,
    *,
    client_label: str = "brain",
    ts: datetime | None = None,
    turn_id: str | None = "turn_000001",
    call_type: str = "chat_with_tools",
    image_count: int = 0,
) -> RequestMetrics:
    now = ts or datetime(2026, 4, 11, 12, 0, tzinfo=UTC)
    return RequestMetrics(
        ts=now,
        request_id=request_id,
        turn_id=turn_id,
        round=1,
        client_label=client_label,
        provider="openrouter",
        model="claude-sonnet-4.5",
        call_type=call_type,
        message_count=2,
        tool_count=1 if call_type == "chat_with_tools" else 0,
        image_count=image_count,
        has_response_schema=False,
        temperature=None,
    )


def test_session_summary_uses_read_cache_rate_and_marks_codex_write_unmeasurable():
    cache = MetricsCache(Path("/tmp"), {})
    cache._files["s1"] = SessionFiles(session_dir=Path("/tmp/s1"), meta=_meta("s1"))
    cache._responses["s1"] = [
        ResponseMetrics(
            ts=datetime(2026, 4, 11, 12, 0, tzinfo=UTC),
            round=1,
            provider="codex",
            model="gpt-5.4",
            prompt_tokens=1000,
            completion_tokens=100,
            cache_read_tokens=900,
            cache_write_tokens=0,
            latency_ms=100,
            cost=None,
            turn_id="turn_000001",
        )
    ]
    cache._turns["s1"] = []

    summary = cache.get_session_summary("s1")

    assert summary is not None
    assert summary.read_cache_rate == 0.9
    assert summary.write_cache_measurable is False


def test_all_requests_reports_openrouter_write_cache_as_measurable():
    cache = MetricsCache(Path("/tmp"), {})
    cache._files["s1"] = SessionFiles(session_dir=Path("/tmp/s1"), meta=_meta("s1"))
    cache._requests["s1"] = [_request("req_000001")]
    cache._responses["s1"] = [
        ResponseMetrics(
            ts=datetime(2026, 4, 11, 12, 0, tzinfo=UTC),
            round=1,
            provider="openrouter",
            model="claude-sonnet-4.5",
            prompt_tokens=2000,
            completion_tokens=100,
            cache_read_tokens=1000,
            cache_write_tokens=128,
            latency_ms=100,
            cost=None,
            turn_id="turn_000001",
            request_id="req_000001",
        )
    ]

    rows = cache.get_all_requests(date(2026, 4, 11), date(2026, 4, 11))

    assert rows[0]["read_cache_rate"] == 0.5
    assert rows[0]["write_cache_measurable"] is True


def test_all_requests_reports_pricing_source_status():
    cache = MetricsCache(Path("/tmp"), {})
    cache._files["s1"] = SessionFiles(session_dir=Path("/tmp/s1"), meta=_meta("s1"))
    cache._requests["s1"] = [_request("req_000001")]
    cache._responses["s1"] = [
        ResponseMetrics(
            ts=datetime(2026, 4, 11, 12, 0, tzinfo=UTC),
            round=1,
            provider="deepseek",
            model="deepseek-v4-pro",
            prompt_tokens=2000,
            completion_tokens=100,
            cache_read_tokens=1000,
            cache_write_tokens=0,
            latency_ms=100,
            cost=0.001,
            turn_id="turn_000001",
            request_id="req_000001",
            pricing_source="local_override",
            pricing_source_url="https://api-docs.deepseek.com/quick_start/pricing",
            pricing_stale=False,
        )
    ]

    rows = cache.get_all_requests(date(2026, 4, 11), date(2026, 4, 11))
    summary = cache.get_session_summary("s1")

    assert rows[0]["pricing_source"] == "local_override"
    assert rows[0]["pricing_stale"] is False
    assert summary is not None
    assert summary.pricing_sources == [
        {
            "source": "local_override",
            "source_url": "https://api-docs.deepseek.com/quick_start/pricing",
            "stale": False,
            "count": 1,
        }
    ]


def test_ollama_session_summary_marks_read_cache_rate_unavailable():
    cache = MetricsCache(Path("/tmp"), {})
    cache._files["s1"] = SessionFiles(session_dir=Path("/tmp/s1"), meta=_meta("s1"))
    cache._responses["s1"] = [
        ResponseMetrics(
            ts=datetime(2026, 4, 11, 12, 0, tzinfo=UTC),
            round=1,
            provider="ollama",
            model="kimi-k2.6:cloud",
            prompt_tokens=1000,
            completion_tokens=100,
            cache_read_tokens=0,
            cache_write_tokens=0,
            latency_ms=100,
            cost=None,
            turn_id="turn_000001",
        )
    ]
    cache._turns["s1"] = []

    summary = cache.get_session_summary("s1")

    assert summary is not None
    assert summary.read_cache_rate is None


def test_all_requests_reports_ollama_read_cache_rate_unavailable():
    cache = MetricsCache(Path("/tmp"), {})
    cache._files["s1"] = SessionFiles(session_dir=Path("/tmp/s1"), meta=_meta("s1"))
    cache._requests["s1"] = [_request("req_000001")]
    cache._responses["s1"] = [
        ResponseMetrics(
            ts=datetime(2026, 4, 11, 12, 0, tzinfo=UTC),
            round=1,
            provider="ollama",
            model="kimi-k2.6:cloud",
            prompt_tokens=2000,
            completion_tokens=100,
            cache_read_tokens=0,
            cache_write_tokens=0,
            latency_ms=100,
            cost=None,
            turn_id="turn_000001",
            request_id="req_000001",
        )
    ]

    rows = cache.get_all_requests(date(2026, 4, 11), date(2026, 4, 11))

    assert rows[0]["read_cache_rate"] is None


def test_all_requests_includes_request_without_response():
    cache = MetricsCache(Path("/tmp"), {})
    cache._files["s1"] = SessionFiles(session_dir=Path("/tmp/s1"), meta=_meta("s1"))
    cache._requests["s1"] = [_request("req_000001", client_label="gui_worker")]

    rows = cache.get_all_requests(date(2026, 4, 11), date(2026, 4, 11))

    assert rows[0]["request_id"] == "req_000001"
    assert rows[0]["client_label"] == "gui_worker"
    assert rows[0]["status"] == "pending"
    assert rows[0]["prompt_tokens"] is None


def test_all_requests_reports_text_response_and_error_status():
    cache = MetricsCache(Path("/tmp"), {})
    cache._files["s1"] = SessionFiles(session_dir=Path("/tmp/s1"), meta=_meta("s1"))
    cache._requests["s1"] = [
        _request("req_000001", call_type="chat"),
        _request("req_000002", call_type="chat"),
    ]
    cache._responses["s1"] = [
        ResponseMetrics(
            ts=datetime(2026, 4, 11, 12, 1, tzinfo=UTC),
            round=1,
            provider="openrouter",
            model="claude-sonnet-4.5",
            prompt_tokens=0,
            completion_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            latency_ms=100,
            cost=None,
            turn_id="turn_000001",
            request_id="req_000001",
            call_type="chat",
            usage_available=False,
            response_text="ok",
        ),
        ResponseMetrics(
            ts=datetime(2026, 4, 11, 12, 2, tzinfo=UTC),
            round=1,
            provider="openrouter",
            model="claude-sonnet-4.5",
            prompt_tokens=0,
            completion_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            latency_ms=100,
            cost=None,
            turn_id="turn_000001",
            request_id="req_000002",
            call_type="chat",
            usage_available=False,
            error="RuntimeError: failed",
        ),
    ]

    rows = cache.get_all_requests(date(2026, 4, 11), date(2026, 4, 11))

    rows_by_id = {row["request_id"]: row for row in rows}
    assert rows_by_id["req_000001"]["status"] == "completed"
    assert rows_by_id["req_000001"]["usage_available"] is False
    assert rows_by_id["req_000002"]["status"] == "failed"
    assert rows_by_id["req_000002"]["error"] == "RuntimeError: failed"


def test_all_requests_filters_by_client_label_and_request_date():
    cache = MetricsCache(Path("/tmp"), {})
    cache._files["s1"] = SessionFiles(session_dir=Path("/tmp/s1"), meta=_meta("s1"))
    cache._requests["s1"] = [
        _request(
            "req_000001",
            client_label="brain",
            ts=datetime(2026, 4, 10, 23, 59, tzinfo=UTC),
        ),
        _request(
            "req_000002",
            client_label="gui_manager",
            ts=datetime(2026, 4, 11, 12, 0, tzinfo=UTC),
        ),
        _request(
            "req_000003",
            client_label="brain",
            ts=datetime(2026, 4, 11, 12, 1, tzinfo=UTC),
        ),
    ]

    rows = cache.get_all_requests(
        date(2026, 4, 11),
        date(2026, 4, 11),
        client_label="brain",
    )
    labels = cache.get_client_labels_in_range(date(2026, 4, 11), date(2026, 4, 11))

    assert [row["request_id"] for row in rows] == ["req_000003"]
    assert labels == ["brain", "gui_manager"]


def test_request_detail_sanitizes_image_data(tmp_path: Path):
    session_dir = tmp_path / "s1"
    session_dir.mkdir()
    import base64
    import io

    from PIL import Image

    img = Image.new("RGB", (20, 10), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    image_data = base64.b64encode(buf.getvalue()).decode("ascii")
    record = SessionLLMRequestRecord(
        seq=1,
        ts=datetime(2026, 4, 11, 12, 0, tzinfo=UTC),
        session_id="s1",
        turn_id="turn_000001",
        request_id="req_000001",
        round=1,
        client_label="gui_worker",
        provider="openrouter",
        model="gemini",
        call_type="chat",
        messages=[
            Message(role="system", content="system"),
            Message(
                role="user",
                content=[
                    ContentPart(
                        type="image",
                        media_type="image/jpeg",
                        data=image_data,
                        width=20,
                        height=10,
                    ),
                    ContentPart(type="text", text="find button"),
                ],
            ),
        ],
    )
    (session_dir / "requests.jsonl").write_text(
        record.model_dump_json() + "\n",
        encoding="utf-8",
    )
    cache = MetricsCache(tmp_path, {})
    cache._files["s1"] = SessionFiles(session_dir=session_dir, meta=_meta("s1"))

    detail = cache.get_request_detail("s1", "req_000001")

    assert detail is not None
    image_part = detail["messages"][1]["content"][0]
    assert image_part["type"] == "image"
    assert "data" not in image_part
    assert image_part["data_size_bytes"] > 0
    assert image_part["thumbnail_data_url"].startswith("data:image/jpeg;base64,")
