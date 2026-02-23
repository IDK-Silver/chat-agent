from pathlib import Path


SRC_ROOT = Path("src/chat_agent")


def _python_files() -> list[Path]:
    return [p for p in SRC_ROOT.rglob("*.py") if p.is_file()]


def test_no_prompt_toolkit_imports_remain():
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        assert "prompt_toolkit" not in text, f"prompt_toolkit import left in {path}"


def test_only_tui_package_imports_textual():
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        has_import = ("import textual" in text) or ("from textual" in text)
        if not has_import:
            continue
        rel = path.as_posix()
        assert rel.startswith("src/chat_agent/tui/"), f"textual import outside tui package: {rel}"


def test_legacy_prompt_toolkit_files_removed():
    assert not (SRC_ROOT / "cli" / "input.py").exists()
    assert not (SRC_ROOT / "cli" / "interrupt.py").exists()
    assert not (SRC_ROOT / "cli" / "picker.py").exists()
