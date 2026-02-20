"""Tests for tools/builtin/image.py: read_image tool."""

import base64
from pathlib import Path

import pytest

from chat_agent.llm.schema import ContentPart
from chat_agent.tools.builtin.image import (
    READ_IMAGE_DEFINITION,
    READ_IMAGE_BY_SUBAGENT_DEFINITION,
    _read_image_data,
    create_read_image_vision,
    create_read_image_with_sub_agent,
    create_read_image_by_subagent,
)


@pytest.fixture()
def tmp_image(tmp_path: Path) -> Path:
    """Create a minimal 1x1 red PNG."""
    from PIL import Image
    img = Image.new("RGB", (10, 20), color="red")
    path = tmp_path / "test.png"
    img.save(path)
    return path


@pytest.fixture()
def allowed_paths(tmp_path: Path) -> list[str]:
    return [str(tmp_path)]


class TestReadImageData:
    def test_reads_valid_image(self, tmp_image: Path, allowed_paths: list[str], tmp_path: Path):
        b64, media_type, w, h = _read_image_data(
            str(tmp_image), allowed_paths, tmp_path,
        )
        assert media_type == "image/png"
        assert w == 10
        assert h == 20
        # Verify base64 is valid
        raw = base64.b64decode(b64)
        assert len(raw) > 0

    def test_file_not_found(self, allowed_paths: list[str], tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Image not found"):
            _read_image_data(str(tmp_path / "missing.png"), allowed_paths, tmp_path)

    def test_path_not_allowed(self, tmp_image: Path, tmp_path: Path):
        with pytest.raises(ValueError, match="Path not allowed"):
            _read_image_data(str(tmp_image), ["/some/other/path"], tmp_path)

    def test_unsupported_format(self, tmp_path: Path):
        txt = tmp_path / "file.txt"
        txt.write_text("hello")
        with pytest.raises(ValueError, match="Unsupported image format"):
            _read_image_data(str(txt), [str(tmp_path)], tmp_path)


class TestCreateReadImageVision:
    def test_returns_content_parts(self, tmp_image: Path, allowed_paths: list[str], tmp_path: Path):
        fn = create_read_image_vision(allowed_paths, tmp_path)
        result = fn(path=str(tmp_image))
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0].type == "text"
        assert "10x20" in (result[0].text or "")
        assert result[1].type == "image"
        assert result[1].media_type == "image/png"
        assert result[1].data is not None

    def test_error_missing_path(self, allowed_paths: list[str], tmp_path: Path):
        fn = create_read_image_vision(allowed_paths, tmp_path)
        result = fn(path="")
        assert isinstance(result, str)
        assert "Error" in result

    def test_error_file_not_found(self, allowed_paths: list[str], tmp_path: Path):
        fn = create_read_image_vision(allowed_paths, tmp_path)
        result = fn(path=str(tmp_path / "nope.png"))
        assert isinstance(result, str)
        assert "Error" in result


class TestCreateReadImageWithSubAgent:
    def test_returns_description(self, tmp_image: Path, allowed_paths: list[str], tmp_path: Path):
        class FakeVisionAgent:
            def describe(self, image_parts):
                return "A red image"

        fn = create_read_image_with_sub_agent(allowed_paths, tmp_path, FakeVisionAgent())
        result = fn(path=str(tmp_image))
        assert isinstance(result, str)
        assert "A red image" in result
        assert "10x20" in result

    def test_sub_agent_failure_fallback(self, tmp_image: Path, allowed_paths: list[str], tmp_path: Path):
        class FailingAgent:
            def describe(self, image_parts):
                raise RuntimeError("connection error")

        fn = create_read_image_with_sub_agent(allowed_paths, tmp_path, FailingAgent())
        result = fn(path=str(tmp_image))
        assert isinstance(result, str)
        assert "unavailable" in result.lower() or "connection error" in result


class TestTildeExpansion:
    def test_tilde_expands_to_home(self, tmp_path: Path, monkeypatch):
        """~ in path should be expanded before path checks."""
        from PIL import Image
        # Create image inside a fake home dir
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        img = Image.new("RGB", (2, 2), color="blue")
        img_path = fake_home / "pic.png"
        img.save(img_path)

        monkeypatch.setenv("HOME", str(fake_home))
        # Re-read since Path.home() caches
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        b64, media_type, w, h = _read_image_data(
            "~/pic.png", [str(fake_home)], tmp_path,
        )
        assert media_type == "image/png"
        assert w == 2


class TestCreateReadImageBySubagent:
    def test_returns_description_with_context(self, tmp_image: Path, allowed_paths: list[str], tmp_path: Path):
        class FakeVisionAgent:
            def describe(self, image_parts):
                # Verify context is passed as the text part
                assert image_parts[0].type == "text"
                assert "sticker" in (image_parts[0].text or "")
                return "The sticker was sent successfully"

        fn = create_read_image_by_subagent(allowed_paths, tmp_path, FakeVisionAgent())
        result = fn(path=str(tmp_image), context="Check if the sticker was sent")
        assert isinstance(result, str)
        assert "sticker was sent successfully" in result
        assert "10x20" in result

    def test_context_required(self, tmp_image: Path, allowed_paths: list[str], tmp_path: Path):
        class FakeVisionAgent:
            def describe(self, image_parts):
                return "ok"

        fn = create_read_image_by_subagent(allowed_paths, tmp_path, FakeVisionAgent())
        result = fn(path=str(tmp_image), context="")
        assert "Error" in result

    def test_sub_agent_failure_fallback(self, tmp_image: Path, allowed_paths: list[str], tmp_path: Path):
        class FailingAgent:
            def describe(self, image_parts):
                raise RuntimeError("connection error")

        fn = create_read_image_by_subagent(allowed_paths, tmp_path, FailingAgent())
        result = fn(path=str(tmp_image), context="Describe this")
        assert isinstance(result, str)
        assert "unavailable" in result.lower() or "connection error" in result


class TestImageResize:
    """Verify automatic downscaling for large images."""

    def test_large_image_resized(self, tmp_path: Path):
        """Image exceeding MAX_LONG_EDGE is resized and converted to JPEG."""
        from PIL import Image
        from chat_agent.tools.builtin.image import MAX_LONG_EDGE

        img = Image.new("RGB", (3000, 2000), color="green")
        path = tmp_path / "big.png"
        img.save(path)

        b64, media_type, w, h = _read_image_data(str(path), [str(tmp_path)], tmp_path)
        assert max(w, h) <= MAX_LONG_EDGE
        assert media_type == "image/jpeg"
        # Verify aspect ratio preserved
        assert abs(w / h - 3000 / 2000) < 0.01

    def test_tall_image_resized(self, tmp_path: Path):
        """Portrait image: height is the long edge."""
        from PIL import Image
        from chat_agent.tools.builtin.image import MAX_LONG_EDGE

        img = Image.new("RGB", (1000, 3000), color="blue")
        path = tmp_path / "tall.png"
        img.save(path)

        b64, media_type, w, h = _read_image_data(str(path), [str(tmp_path)], tmp_path)
        assert h <= MAX_LONG_EDGE
        assert media_type == "image/jpeg"
        assert abs(w / h - 1000 / 3000) < 0.01

    def test_small_image_unchanged(self, tmp_path: Path):
        """Image within limits is not modified."""
        from PIL import Image

        img = Image.new("RGB", (800, 600), color="red")
        path = tmp_path / "small.png"
        img.save(path)

        b64, media_type, w, h = _read_image_data(str(path), [str(tmp_path)], tmp_path)
        assert w == 800
        assert h == 600
        assert media_type == "image/png"

    def test_rgba_image_resized_to_rgb(self, tmp_path: Path):
        """RGBA PNG is converted to RGB JPEG when resized."""
        from PIL import Image

        img = Image.new("RGBA", (4000, 2000), color=(255, 0, 0, 128))
        path = tmp_path / "alpha.png"
        img.save(path)

        b64, media_type, w, h = _read_image_data(str(path), [str(tmp_path)], tmp_path)
        assert media_type == "image/jpeg"
        # Decoded bytes should be valid JPEG
        raw = base64.b64decode(b64)
        assert raw[:2] == b"\xff\xd8"  # JPEG magic bytes


class TestReadImageDefinition:
    def test_definition_structure(self):
        assert READ_IMAGE_DEFINITION.name == "read_image"
        assert "path" in READ_IMAGE_DEFINITION.parameters
        assert "path" in READ_IMAGE_DEFINITION.required


class TestReadImageBySubagentDefinition:
    def test_definition_structure(self):
        assert READ_IMAGE_BY_SUBAGENT_DEFINITION.name == "read_image_by_subagent"
        assert "path" in READ_IMAGE_BY_SUBAGENT_DEFINITION.parameters
        assert "context" in READ_IMAGE_BY_SUBAGENT_DEFINITION.parameters
        assert "path" in READ_IMAGE_BY_SUBAGENT_DEFINITION.required
        assert "context" in READ_IMAGE_BY_SUBAGENT_DEFINITION.required
