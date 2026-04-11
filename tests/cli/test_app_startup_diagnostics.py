from chat_agent.cli.app import _emit_pre_tui_message


class _DummyConsole:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def print_error(self, message: str) -> None:
        self.calls.append(("error", message))

    def print_warning(self, message: str) -> None:
        self.calls.append(("warning", message))

    def print_info(self, message: str) -> None:
        self.calls.append(("info", message))


def test_emit_pre_tui_message_mirrors_to_stderr(capsys):
    console = _DummyConsole()

    _emit_pre_tui_message(console, "error", "boom")

    assert console.calls == [("error", "boom")]
    assert "[chat-cli startup] boom" in capsys.readouterr().err
