"""Tests for session JSONL round-trip with multimodal Message content."""

from chat_agent.llm.schema import ContentPart, Message


class TestMultimodalRoundtrip:
    def test_message_with_content_parts_roundtrip(self):
        """Message with list[ContentPart] survives JSON serialization and deserialization."""
        original = Message(
            role="tool",
            content=[
                ContentPart(type="text", text="Image loaded"),
                ContentPart(
                    type="image",
                    media_type="image/png",
                    data="abc123base64data",
                    width=640,
                    height=480,
                ),
            ],
            tool_call_id="call_1",
            name="read_image",
        )

        json_str = original.model_dump_json()
        restored = Message.model_validate_json(json_str)

        assert restored.role == "tool"
        assert restored.tool_call_id == "call_1"
        assert restored.name == "read_image"
        assert isinstance(restored.content, list)
        assert len(restored.content) == 2

        text_part = restored.content[0]
        assert text_part.type == "text"
        assert text_part.text == "Image loaded"

        image_part = restored.content[1]
        assert image_part.type == "image"
        assert image_part.media_type == "image/png"
        assert image_part.data == "abc123base64data"
        assert image_part.width == 640
        assert image_part.height == 480

    def test_string_content_roundtrip_unchanged(self):
        """Regular string content still round-trips correctly."""
        original = Message(role="user", content="hello world")
        json_str = original.model_dump_json()
        restored = Message.model_validate_json(json_str)
        assert restored.content == "hello world"

    def test_none_content_roundtrip(self):
        """None content still round-trips correctly."""
        original = Message(role="assistant", content=None)
        json_str = original.model_dump_json()
        restored = Message.model_validate_json(json_str)
        assert restored.content is None

    def test_session_file_format(self, tmp_path):
        """Simulate JSONL append and reload with multimodal content."""
        jsonl_path = tmp_path / "messages.jsonl"

        messages = [
            Message(role="user", content="Show me the image"),
            Message(
                role="tool",
                content=[
                    ContentPart(type="text", text="[Image: photo.jpg (800x600)]"),
                    ContentPart(type="image", media_type="image/jpeg", data="base64data", width=800, height=600),
                ],
                tool_call_id="tc1",
                name="read_image",
            ),
            Message(role="assistant", content="I can see a photo."),
        ]

        # Write JSONL
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(msg.model_dump_json() + "\n")

        # Read back
        restored = []
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    restored.append(Message.model_validate_json(line))

        assert len(restored) == 3
        assert restored[0].content == "Show me the image"
        assert isinstance(restored[1].content, list)
        assert len(restored[1].content) == 2
        assert restored[2].content == "I can see a photo."
