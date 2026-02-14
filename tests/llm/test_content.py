"""Tests for llm/content.py: content_to_text and content_char_estimate."""

from chat_agent.llm.content import content_char_estimate, content_to_text
from chat_agent.llm.schema import ContentPart


class TestContentToText:
    def test_none(self):
        assert content_to_text(None) == ""

    def test_string(self):
        assert content_to_text("hello") == "hello"

    def test_empty_string(self):
        assert content_to_text("") == ""

    def test_text_parts(self):
        parts = [
            ContentPart(type="text", text="hello "),
            ContentPart(type="text", text="world"),
        ]
        assert content_to_text(parts) == "hello world"

    def test_skips_image_parts(self):
        parts = [
            ContentPart(type="text", text="before"),
            ContentPart(type="image", media_type="image/png", data="abc", width=100, height=100),
            ContentPart(type="text", text=" after"),
        ]
        assert content_to_text(parts) == "before after"

    def test_image_only(self):
        parts = [
            ContentPart(type="image", media_type="image/png", data="abc", width=100, height=100),
        ]
        assert content_to_text(parts) == ""

    def test_empty_list(self):
        assert content_to_text([]) == ""

    def test_text_part_with_none_text(self):
        parts = [ContentPart(type="text", text=None)]
        assert content_to_text(parts) == ""


class TestContentCharEstimate:
    def test_none(self):
        assert content_char_estimate(None) == 0

    def test_string(self):
        assert content_char_estimate("hello") == 5

    def test_text_parts(self):
        parts = [
            ContentPart(type="text", text="hello"),
            ContentPart(type="text", text="world"),
        ]
        assert content_char_estimate(parts) == 10

    def test_image_openai(self):
        parts = [
            ContentPart(type="image", media_type="image/png", data="x", width=1024, height=1024),
        ]
        estimate = content_char_estimate(parts, "openai")
        # (170 * ceil(1024/512) * ceil(1024/512) + 85) * 4
        # = (170 * 2 * 2 + 85) * 4 = (680 + 85) * 4 = 765 * 4 = 3060
        assert estimate == 3060

    def test_image_copilot(self):
        parts = [
            ContentPart(type="image", media_type="image/png", data="x", width=512, height=512),
        ]
        estimate = content_char_estimate(parts, "copilot")
        # (170 * 1 * 1 + 85) * 4 = 255 * 4 = 1020
        assert estimate == 1020

    def test_image_anthropic(self):
        parts = [
            ContentPart(type="image", media_type="image/png", data="x", width=1000, height=1000),
        ]
        estimate = content_char_estimate(parts, "anthropic")
        # (1000 * 1000) // 750 * 4 = 1333 * 4 = 5332
        assert estimate == 5332

    def test_image_gemini(self):
        parts = [
            ContentPart(type="image", media_type="image/png", data="x", width=2000, height=2000),
        ]
        estimate = content_char_estimate(parts, "gemini")
        # 258 * 4 = 1032 (flat rate)
        assert estimate == 1032

    def test_mixed_content(self):
        parts = [
            ContentPart(type="text", text="hello"),  # 5 chars
            ContentPart(type="image", media_type="image/png", data="x", width=512, height=512),
        ]
        estimate = content_char_estimate(parts, "openai")
        # 5 + (170 * 1 * 1 + 85) * 4 = 5 + 1020 = 1025
        assert estimate == 1025

    def test_image_zero_dimensions(self):
        parts = [
            ContentPart(type="image", media_type="image/png", data="x", width=0, height=0),
        ]
        # Should fallback to 1000 for unknown dimensions
        assert content_char_estimate(parts, "openai") == 1000
        assert content_char_estimate(parts, "anthropic") == 1000
