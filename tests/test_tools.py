"""Tests for the tool registry and built-in tools."""

import json
from pathlib import Path

import pytest

from chat_agent.llm.schema import ToolCall, ToolDefinition, ToolParameter
from chat_agent.tools import ToolRegistry, get_current_time
from chat_agent.memory import MEMORY_EDIT_DEFINITION
from chat_agent.tools.builtin import (
    GET_CURRENT_TIME_DEFINITION,
    create_read_file,
    create_write_file,
    create_edit_file,
)


class TestToolRegistry:
    def test_register_and_get_definitions(self):
        registry = ToolRegistry()
        definition = ToolDefinition(
            name="test_tool",
            description="A test tool",
            parameters={
                "arg1": ToolParameter(type="string", description="First argument"),
            },
            required=["arg1"],
        )

        def test_func(arg1: str) -> str:
            return f"Result: {arg1}"

        registry.register("test_tool", test_func, definition)

        definitions = registry.get_definitions()
        assert len(definitions) == 1
        assert definitions[0].name == "test_tool"

    def test_register_name_mismatch(self):
        registry = ToolRegistry()
        definition = ToolDefinition(
            name="actual_name",
            description="A test tool",
            parameters={},
        )

        def test_func() -> str:
            return "result"

        with pytest.raises(ValueError, match="name mismatch"):
            registry.register("different_name", test_func, definition)

    def test_execute_success(self):
        registry = ToolRegistry()
        definition = ToolDefinition(
            name="echo",
            description="Echo the input",
            parameters={
                "message": ToolParameter(type="string", description="Message to echo"),
            },
            required=["message"],
        )

        def echo_func(message: str) -> str:
            return f"Echo: {message}"

        registry.register("echo", echo_func, definition)

        tool_call = ToolCall(id="call_1", name="echo", arguments={"message": "hello"})
        result = registry.execute(tool_call)
        assert result == "Echo: hello"

    def test_execute_unknown_tool(self):
        registry = ToolRegistry()
        tool_call = ToolCall(id="call_1", name="unknown", arguments={})
        result = registry.execute(tool_call)
        assert "Unknown tool" in result

    def test_execute_with_error(self):
        registry = ToolRegistry()
        definition = ToolDefinition(
            name="failing",
            description="A tool that fails",
            parameters={},
        )

        def failing_func() -> str:
            raise RuntimeError("Intentional error")

        registry.register("failing", failing_func, definition)

        tool_call = ToolCall(id="call_1", name="failing", arguments={})
        result = registry.execute(tool_call)
        assert "Error executing" in result
        assert "Intentional error" in result

    def test_has_tool(self):
        registry = ToolRegistry()
        definition = ToolDefinition(
            name="exists",
            description="A tool that exists",
            parameters={},
        )

        registry.register("exists", lambda: "ok", definition)

        assert registry.has_tool("exists") is True
        assert registry.has_tool("not_exists") is False


class TestBuiltinTools:
    def test_get_current_time_utc(self):
        result = get_current_time("UTC")
        assert "UTC" in result
        # Check format: YYYY-MM-DD HH:MM:SS UTC
        assert len(result.split()) == 3
        assert result.endswith("UTC")

    def test_get_current_time_default(self):
        result = get_current_time()
        assert "UTC+8" in result

    def test_get_current_time_other_timezone(self):
        result = get_current_time("America/New_York")
        assert "America/New_York" in result
        assert len(result.split()) == 3

    def test_get_current_time_asia_taipei(self):
        result = get_current_time("Asia/Taipei")
        assert "Asia/Taipei" in result

    def test_get_current_time_invalid_timezone(self):
        result = get_current_time("Invalid/Timezone")
        assert "Error" in result

    def test_get_current_time_definition(self):
        assert GET_CURRENT_TIME_DEFINITION.name == "get_current_time"
        assert "timezone" in GET_CURRENT_TIME_DEFINITION.parameters


class TestToolDefinition:
    def test_to_json_schema(self):
        definition = ToolDefinition(
            name="test",
            description="Test tool",
            parameters={
                "name": ToolParameter(type="string", description="The name"),
                "count": ToolParameter(type="integer", description="The count"),
            },
            required=["name"],
        )

        schema = definition.to_json_schema()

        assert schema["type"] == "object"
        assert "name" in schema["properties"]
        assert schema["properties"]["name"]["type"] == "string"
        assert schema["required"] == ["name"]

    def test_to_json_schema_with_enum(self):
        definition = ToolDefinition(
            name="test",
            description="Test tool",
            parameters={
                "level": ToolParameter(
                    type="string",
                    description="The level",
                    enum=["low", "medium", "high"],
                ),
            },
        )

        schema = definition.to_json_schema()

        assert schema["properties"]["level"]["enum"] == ["low", "medium", "high"]

    def test_to_json_schema_with_custom_nested_schema(self):
        definition = ToolDefinition(
            name="test",
            description="Test tool",
            parameters={
                "requests": ToolParameter(
                    type="array",
                    description="request list",
                    json_schema={
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "request_id": {"type": "string"},
                            },
                            "required": ["request_id"],
                        },
                    },
                ),
            },
            required=["requests"],
        )

        schema = definition.to_json_schema()

        assert schema["properties"]["requests"]["type"] == "array"
        assert schema["properties"]["requests"]["items"]["type"] == "object"
        assert (
            schema["properties"]["requests"]["items"]["properties"]["request_id"]["type"]
            == "string"
        )
        assert schema["properties"]["requests"]["items"]["required"] == ["request_id"]

    def test_memory_edit_schema_defines_requests_items(self):
        schema = MEMORY_EDIT_DEFINITION.to_json_schema()

        requests_schema = schema["properties"]["requests"]
        assert requests_schema["type"] == "array"
        assert requests_schema["minItems"] == 1
        assert requests_schema["maxItems"] == 12
        assert requests_schema["items"]["type"] == "object"
        assert "request_id" in requests_schema["items"]["properties"]
        assert "instruction" in requests_schema["items"]["properties"]
        assert "target_path" in requests_schema["items"]["properties"]


class TestFileTools:
    def test_read_file_basic(self, tmp_path: Path):
        """read_file returns content with line numbers wrapped in XML tags."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3")

        read_file = create_read_file([], tmp_path)
        result = read_file(str(test_file))

        assert result.startswith(f'<file path="{test_file}" lines="1-3" total_lines="3">')
        assert result.endswith("</file>")
        assert "1\tline1" in result
        assert "2\tline2" in result
        assert "3\tline3" in result

    def test_read_file_offset_limit(self, tmp_path: Path):
        """read_file respects offset and limit."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3\nline4\nline5")

        read_file = create_read_file([], tmp_path)
        result = read_file(str(test_file), offset=2, limit=2)

        assert f'lines="2-3" total_lines="5"' in result
        assert "line1" not in result
        assert "2\tline2" in result
        assert "3\tline3" in result
        assert "line4" not in result

    def test_read_file_json_output(self, tmp_path: Path):
        """read_file returns structured JSON when requested."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3")

        read_file = create_read_file([], tmp_path)
        result = read_file(str(test_file), offset=2, limit=2, output_format="json")
        data = json.loads(result)

        assert data["path"] == str(test_file)
        assert data["returned_lines"] == 2
        assert data["total_lines"] == 3
        assert data["start_line"] == 2
        assert data["end_line"] == 3
        assert data["lines"][0] == {"line": 2, "content": "line2"}
        assert data["lines"][1] == {"line": 3, "content": "line3"}

    def test_read_file_invalid_output_format(self, tmp_path: Path):
        """read_file validates output_format."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1")

        read_file = create_read_file([], tmp_path)
        result = read_file(str(test_file), output_format="yaml")

        assert "Invalid output_format" in result

    def test_read_file_not_found(self, tmp_path: Path):
        """read_file returns error for missing file."""
        read_file = create_read_file([], tmp_path)
        result = read_file(str(tmp_path / "nonexistent.txt"))
        assert "Error" in result
        assert "does not exist" in result

    def test_read_file_binary_detection(self, tmp_path: Path):
        """read_file detects binary files."""
        test_file = tmp_path / "binary.bin"
        test_file.write_bytes(b"hello\x00world")

        read_file = create_read_file([], tmp_path)
        result = read_file(str(test_file))
        assert "binary" in result.lower()

    def test_read_file_path_not_allowed(self, tmp_path: Path):
        """read_file blocks paths outside allowed directories."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        test_file = outside / "secret.txt"
        test_file.write_text("secret")

        read_file = create_read_file([], workspace)
        result = read_file(str(test_file))
        assert "not allowed" in result

    def test_write_file_basic(self, tmp_path: Path):
        """write_file creates file with content."""
        write_file = create_write_file([], tmp_path)
        target = tmp_path / "new.txt"

        result = write_file(str(target), "hello world")

        assert "Successfully" in result
        assert target.read_text() == "hello world"

    def test_write_file_creates_dirs(self, tmp_path: Path):
        """write_file creates parent directories."""
        write_file = create_write_file([], tmp_path)
        target = tmp_path / "nested" / "dir" / "file.txt"

        result = write_file(str(target), "content")

        assert "Successfully" in result
        assert target.exists()

    def test_write_file_blocks_overwrite_non_empty(self, tmp_path: Path):
        """write_file rejects writes to non-empty existing files."""
        test_file = tmp_path / "existing.txt"
        test_file.write_text("old content")

        write_file = create_write_file([], tmp_path)
        result = write_file(str(test_file), "new content")

        assert "Refusing to overwrite non-empty file" in result
        assert test_file.read_text() == "old content"

    def test_write_file_allows_existing_empty_file(self, tmp_path: Path):
        """write_file allows writing to an existing empty file."""
        test_file = tmp_path / "empty.txt"
        test_file.write_text("")

        write_file = create_write_file([], tmp_path)
        result = write_file(str(test_file), "filled")

        assert "Successfully wrote" in result
        assert test_file.read_text() == "filled"

    def test_write_file_rejects_directory_target(self, tmp_path: Path):
        """write_file rejects directory paths."""
        target_dir = tmp_path / "somedir"
        target_dir.mkdir()

        write_file = create_write_file([], tmp_path)
        result = write_file(str(target_dir), "content")

        assert "is not a file" in result

    def test_write_file_path_not_allowed(self, tmp_path: Path):
        """write_file blocks paths outside allowed directories."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside" / "file.txt"

        write_file = create_write_file([], workspace)
        result = write_file(str(outside), "content")
        assert "not allowed" in result

    def test_edit_file_basic(self, tmp_path: Path):
        """edit_file replaces string."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        edit_file = create_edit_file([], tmp_path)
        result = edit_file(str(test_file), "world", "universe")

        assert "Successfully" in result
        assert test_file.read_text() == "hello universe"

    def test_edit_file_uniqueness_check(self, tmp_path: Path):
        """edit_file requires unique string by default."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello hello hello")

        edit_file = create_edit_file([], tmp_path)
        result = edit_file(str(test_file), "hello", "hi")

        assert "Error" in result
        assert "3 times" in result

    def test_edit_file_replace_all(self, tmp_path: Path):
        """edit_file with replace_all replaces all occurrences."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello hello hello")

        edit_file = create_edit_file([], tmp_path)
        result = edit_file(str(test_file), "hello", "hi", replace_all=True)

        assert "Successfully" in result
        assert "3 occurrence" in result
        assert test_file.read_text() == "hi hi hi"

    def test_edit_file_not_found_string(self, tmp_path: Path):
        """edit_file returns error if string not found."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        edit_file = create_edit_file([], tmp_path)
        result = edit_file(str(test_file), "xyz", "abc")

        assert "Error" in result
        assert "not found" in result
        assert "Hint:" in result

    def test_edit_file_not_found_gives_similarity_hint(self, tmp_path: Path):
        """edit_file includes similar line hints when exact match fails."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("- [ ] task one\n- [x] task two")

        edit_file = create_edit_file([], tmp_path)
        result = edit_file(str(test_file), "- [ ] task tow", "- [x] task two")

        assert "not found" in result
        assert "Similar lines:" in result

    def test_edit_file_not_found_file(self, tmp_path: Path):
        """edit_file returns error for missing file."""
        edit_file = create_edit_file([], tmp_path)
        result = edit_file(str(tmp_path / "nonexistent.txt"), "a", "b")
        assert "Error" in result
        assert "does not exist" in result

    def test_edit_file_path_not_allowed(self, tmp_path: Path):
        """edit_file blocks paths outside allowed directories."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        test_file = outside / "file.txt"
        test_file.write_text("content")

        edit_file = create_edit_file([], workspace)
        result = edit_file(str(test_file), "content", "new")
        assert "not allowed" in result
