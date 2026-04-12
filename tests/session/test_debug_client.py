from chat_agent.llm.schema import Message
from chat_agent.session.debug_client import wrap_llm_client_with_session_debug


class _Sink:
    def begin_llm_request(self, **kwargs):
        return None

    def complete_llm_response(self, pending, *, response, latency_ms):
        return None

    def complete_llm_text_response(self, pending, *, response_text, latency_ms):
        return None

    def fail_llm_request(self, pending, *, error, latency_ms):
        return None


class _Client:
    def chat(self, messages, response_schema=None, temperature=None):
        return "ok"

    def chat_with_tools(self, messages, tools, temperature=None):
        raise NotImplementedError

    def compact_messages(self, messages, tools=None):
        return [Message(role="assistant", content="compact-ok")]


def test_debug_wrapper_preserves_compact_messages():
    wrapped = wrap_llm_client_with_session_debug(
        _Client(),
        sink=_Sink(),
        client_label="brain",
        provider="codex",
        model="gpt-5.4",
    )

    assert hasattr(wrapped, "compact_messages")
    result = wrapped.compact_messages([Message(role="user", content="hi")])
    assert result == [Message(role="assistant", content="compact-ok")]
