"""Tests for adapters.formatting module."""

from chat_agent.agent.adapters.formatting import markdown_to_plaintext


class TestMarkdownToPlaintext:

    def test_empty_string(self):
        assert markdown_to_plaintext("") == ""

    def test_plain_text_unchanged(self):
        text = "Hello, how are you today?"
        assert markdown_to_plaintext(text) == text

    # --- Bold ---

    def test_bold_asterisks(self):
        assert markdown_to_plaintext("this is **bold** text") == "this is bold text"

    def test_bold_underscores(self):
        assert markdown_to_plaintext("this is __bold__ text") == "this is bold text"

    # --- Italic ---

    def test_italic_asterisks(self):
        assert markdown_to_plaintext("this is *italic* text") == "this is italic text"

    def test_italic_underscores(self):
        assert markdown_to_plaintext("this is _italic_ text") == "this is italic text"

    def test_preserves_snake_case(self):
        assert markdown_to_plaintext("use my_var_name here") == "use my_var_name here"

    def test_preserves_underscores_in_paths(self):
        text = "file at /path/to_file/my_script.py"
        assert markdown_to_plaintext(text) == text

    # --- Headers ---

    def test_header_h1(self):
        assert markdown_to_plaintext("# Title") == "Title"

    def test_header_h2(self):
        assert markdown_to_plaintext("## Subtitle") == "Subtitle"

    def test_header_h6(self):
        assert markdown_to_plaintext("###### Deep") == "Deep"

    def test_header_multiline(self):
        text = "# Title\n\nSome text\n\n## Section"
        assert markdown_to_plaintext(text) == "Title\n\nSome text\n\nSection"

    # --- Inline code ---

    def test_inline_code(self):
        assert markdown_to_plaintext("use `print()` here") == "use print() here"

    # --- Code fences ---

    def test_code_fence(self):
        text = "before\n```python\nprint('hi')\n```\nafter"
        assert markdown_to_plaintext(text) == "before\nprint('hi')\nafter"

    def test_code_fence_no_language(self):
        text = "```\ncode here\n```"
        assert markdown_to_plaintext(text) == "code here"

    def test_code_fence_preserves_inner_markdown(self):
        text = "```\n**not bold** and # not header\n```"
        result = markdown_to_plaintext(text)
        assert "**not bold**" in result
        assert "# not header" in result

    # --- Links ---

    def test_link(self):
        result = markdown_to_plaintext("[Google](https://google.com)")
        assert result == "Google (https://google.com)"

    def test_link_in_sentence(self):
        result = markdown_to_plaintext("Visit [our site](https://example.com) now")
        assert result == "Visit our site (https://example.com) now"

    # --- Images ---

    def test_image(self):
        assert markdown_to_plaintext("![alt text](image.png)") == "alt text"

    def test_image_empty_alt(self):
        assert markdown_to_plaintext("![](image.png)") == ""

    # --- Blockquotes ---

    def test_blockquote(self):
        assert markdown_to_plaintext("> quoted text") == "quoted text"

    def test_blockquote_multiline(self):
        text = "> line one\n> line two"
        assert markdown_to_plaintext(text) == "line one\nline two"

    # --- Horizontal rules ---

    def test_hr_dashes(self):
        assert markdown_to_plaintext("above\n---\nbelow") == "above\n\nbelow"

    def test_hr_asterisks(self):
        assert markdown_to_plaintext("above\n***\nbelow") == "above\n\nbelow"

    # --- Lists (preserved) ---

    def test_unordered_list(self):
        text = "- item one\n- item two"
        assert markdown_to_plaintext(text) == text

    def test_ordered_list(self):
        text = "1. first\n2. second"
        assert markdown_to_plaintext(text) == text

    # --- Mixed / realistic ---

    def test_mixed_formatting(self):
        text = (
            "## Summary\n\n"
            "Here is **important** info:\n\n"
            "- Visit [docs](https://docs.example.com)\n"
            "- Use `config.yaml` for settings\n\n"
            "> Note: this is a reminder\n\n"
            "---\n\n"
            "Done."
        )
        result = markdown_to_plaintext(text)
        assert result.startswith("Summary")
        assert "**" not in result
        assert "`" not in result
        assert ">" not in result
        assert "---" not in result
        assert "docs (https://docs.example.com)" in result

    def test_collapses_excessive_blank_lines(self):
        text = "line one\n\n\n\n\nline two"
        assert markdown_to_plaintext(text) == "line one\n\nline two"

    def test_idempotent(self):
        text = "## Hello **world**"
        once = markdown_to_plaintext(text)
        twice = markdown_to_plaintext(once)
        assert once == twice
