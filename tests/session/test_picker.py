from datetime import datetime, timezone

from chat_agent.session.picker import pick_session
from chat_agent.session.schema import SessionMetadata


def test_pick_session_formats_created_at_in_app_timezone(monkeypatch):
    printed: list[str] = []

    class FakeConsole:
        def print(self, text, highlight=False):
            printed.append(str(text))

        def input(self, prompt):
            return ""

    monkeypatch.setattr("chat_agent.session.picker.Console", lambda: FakeConsole())

    session = SessionMetadata(
        session_id="s1",
        user_id="u1",
        display_name="User",
        created_at=datetime(2026, 4, 12, 12, 34, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 12, 12, 34, tzinfo=timezone.utc),
        status="active",
        message_count=3,
    )

    result = pick_session([session])

    assert result is None
    assert printed[0] == "[1] [ACTIVE] s1  2026-04-12 20:34  (3 msgs)"
