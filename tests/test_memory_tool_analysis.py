import json

from chat_agent.memory.tool_analysis import summarize_memory_edit_failure


def test_summarize_memory_edit_failure_includes_code_status_and_counts() -> None:
    payload = {
        "status": "failed",
        "turn_id": "t1",
        "applied": [
            {"request_id": "r1", "status": "applied", "path": "memory/people/yu-feng/basic-info.md"},
        ],
        "errors": [
            {
                "request_id": "r2",
                "code": "planner_exception",
                "detail": "Server error '503 Service Unavailable' for url 'http://localhost:4141/v1/chat/completions'",
            }
        ],
        "warnings": [],
    }

    summary = summarize_memory_edit_failure(json.dumps(payload, ensure_ascii=False))

    assert summary is not None
    assert "planner_exception" in summary
    assert "503" in summary
    assert "errors=1" in summary
    assert "applied=1" in summary
