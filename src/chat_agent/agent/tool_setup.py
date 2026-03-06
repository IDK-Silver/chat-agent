"""Tool registry setup for the brain agent."""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
import re
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .contact_map import ContactMap

from ..cli.claude_code_stream_json import (
    extract_text_from_claude_code_stream_json_lines,
)
from ..core.schema import ToolsConfig
from ..gui import (
    GUIManager,
    GUIWorker,
    SCREENSHOT_BY_SUBAGENT_DEFINITION,
    SCREENSHOT_DEFINITION,
    create_screenshot,
    create_screenshot_by_subagent,
)
from ..memory import (
    MEMORY_EDIT_DEFINITION,
    MEMORY_SEARCH_DEFINITION,
    MemoryEditor,
    MemorySearchAgent,
    create_memory_edit,
    create_memory_search,
)
from ..memory.bm25_search import BM25MemorySearch, create_bm25_memory_search
from ..tools import (
    EDIT_FILE_DEFINITION,
    EXECUTE_SHELL_DEFINITION,
    READ_FILE_DEFINITION,
    READ_IMAGE_BY_SUBAGENT_DEFINITION,
    READ_IMAGE_DEFINITION,
    WRITE_FILE_DEFINITION,
    ShellExecutor,
    ToolRegistry,
    VisionAgent,
    create_edit_file,
    create_execute_shell,
    create_read_file,
    create_read_image_by_subagent,
    create_read_image_vision,
    create_read_image_with_sub_agent,
    create_write_file,
)


def _normalize_memory_path(path: str) -> str:
    """Normalize path string for memory path checks."""
    return path.strip().replace("\\", "/")


def _is_memory_path(path: str, *, agent_os_dir: Path) -> bool:
    """Check whether a path points to memory/ in relative or absolute form."""
    normalized = _normalize_memory_path(path)
    if normalized.startswith("./"):
        normalized = normalized[2:]

    if normalized == "memory" or normalized.startswith("memory/"):
        return True
    if normalized.startswith(".agent/memory/"):
        return True

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = agent_os_dir / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to((agent_os_dir / "memory").resolve())
        return True
    except Exception:
        return False


@lru_cache(maxsize=None)
def _build_memory_shell_write_patterns(
    agent_os_dir: Path,
) -> tuple[re.Pattern[str], ...]:
    """Build shell patterns that indicate direct memory writes."""
    memory_abs = re.escape(str((agent_os_dir / "memory").resolve()))
    memory_rel = r"(?:\./)?(?:\.agent/)?memory/"
    memory_target = rf"(?:['\"])?(?:{memory_rel}|{memory_abs}/)"
    return (
        re.compile(rf">>?\s*{memory_target}"),
        re.compile(rf"\btee(?:\s+-a)?\b[^\n]*\s{memory_target}"),
        re.compile(rf"\bsed\s+-i(?:\S*)?\b[^\n]*\s{memory_target}"),
        re.compile(rf"\brm\s[^\n]*{memory_target}"),
        re.compile(rf"\bmv\s[^\n]*{memory_target}"),
    )


def _is_memory_write_shell_command(command: str, *, agent_os_dir: Path) -> bool:
    """Check if command contains shell patterns that write under memory/."""
    return any(
        pattern.search(command) is not None
        for pattern in _build_memory_shell_write_patterns(agent_os_dir)
    )


def setup_tools(
    tools_config: ToolsConfig,
    agent_os_dir: Path,
    *,
    memory_editor: MemoryEditor | None = None,
    memory_search_agent: MemorySearchAgent | None = None,
    bm25_search: BM25MemorySearch | None = None,
    brain_has_vision: bool = False,
    use_own_vision_ability: bool = False,
    vision_agent: VisionAgent | None = None,
    gui_manager: GUIManager | None = None,
    gui_worker: GUIWorker | None = None,
    gui_lock: threading.Lock | None = None,
    screenshot_max_width: int | None = None,
    screenshot_quality: int = 80,
    contact_map: ContactMap | None = None,
    extra_allowed_paths: list[str] | None = None,
    on_shell_stdout_line: Callable[[str], None] | None = None,
    is_shell_cancel_requested: Callable[[], bool] | None = None,
) -> tuple[ToolRegistry, list[str]]:
    """Set up the tool registry with built-in tools."""
    registry = ToolRegistry()

    executor = ShellExecutor(
        agent_os_dir=agent_os_dir,
        blacklist=tools_config.shell.blacklist,
        timeout=tools_config.shell.timeout,
        export_env=tools_config.shell.export_env,
        is_cancel_requested=is_shell_cancel_requested,
    )
    output_transform = (
        extract_text_from_claude_code_stream_json_lines
        if on_shell_stdout_line
        else None
    )
    base_execute_shell = create_execute_shell(
        executor,
        on_stdout_line=on_shell_stdout_line,
        output_transform=output_transform,
    )

    def guarded_execute_shell(command: str, timeout: int | None = None) -> str:
        if _is_memory_write_shell_command(command, agent_os_dir=agent_os_dir):
            return "Error: Direct memory writes via shell are blocked. Use memory_edit."
        return base_execute_shell(command, timeout)

    registry.register("execute_shell", guarded_execute_shell, EXECUTE_SHELL_DEFINITION)

    allowed_paths = list(tools_config.allowed_paths)
    allowed_paths.insert(0, str(agent_os_dir))
    if gui_manager is not None:
        allowed_paths.append(gui_manager.capture_dir)
    if extra_allowed_paths:
        allowed_paths.extend(extra_allowed_paths)

    registry.register(
        "read_file",
        create_read_file(allowed_paths, agent_os_dir),
        READ_FILE_DEFINITION,
    )
    base_write_file = create_write_file(allowed_paths, agent_os_dir)
    base_edit_file = create_edit_file(allowed_paths, agent_os_dir)

    def guarded_write_file(path: str, content: str) -> str:
        if _is_memory_path(path, agent_os_dir=agent_os_dir):
            return "Error: Direct memory writes are blocked. Use memory_edit."
        return base_write_file(path, content)

    def guarded_edit_file(
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        if _is_memory_path(path, agent_os_dir=agent_os_dir):
            return "Error: Direct memory edits are blocked. Use memory_edit."
        return base_edit_file(path, old_string, new_string, replace_all)

    registry.register("write_file", guarded_write_file, WRITE_FILE_DEFINITION)
    registry.register("edit_file", guarded_edit_file, EDIT_FILE_DEFINITION)

    if memory_editor is not None:
        registry.register(
            "memory_edit",
            create_memory_edit(
                memory_editor,
                allowed_paths=allowed_paths,
                base_dir=agent_os_dir,
            ),
            MEMORY_EDIT_DEFINITION,
        )

    if memory_search_agent is not None:
        registry.register(
            "memory_search",
            create_memory_search(
                memory_search_agent,
                allow_failure=tools_config.memory_search.agent.allow_failure,
            ),
            MEMORY_SEARCH_DEFINITION,
        )
    elif bm25_search is not None:
        registry.register(
            "memory_search",
            create_bm25_memory_search(bm25_search),
            MEMORY_SEARCH_DEFINITION,
        )

    if brain_has_vision and not use_own_vision_ability and vision_agent is not None:
        registry.register(
            "read_image_by_subagent",
            create_read_image_by_subagent(allowed_paths, agent_os_dir, vision_agent),
            READ_IMAGE_BY_SUBAGENT_DEFINITION,
        )
    elif brain_has_vision:
        registry.register(
            "read_image",
            create_read_image_vision(allowed_paths, agent_os_dir),
            READ_IMAGE_DEFINITION,
        )
    elif vision_agent is not None:
        registry.register(
            "read_image",
            create_read_image_with_sub_agent(allowed_paths, agent_os_dir, vision_agent),
            READ_IMAGE_DEFINITION,
        )

    if brain_has_vision and not use_own_vision_ability and gui_worker is not None:
        crop_dir = str(agent_os_dir / "tmp")
        registry.register(
            "screenshot_by_subagent",
            create_screenshot_by_subagent(
                gui_worker,
                save_dir=crop_dir,
                gui_lock=gui_lock,
            ),
            SCREENSHOT_BY_SUBAGENT_DEFINITION,
        )
        allowed_paths.append(crop_dir)
    elif brain_has_vision:
        registry.register(
            "screenshot",
            create_screenshot(
                max_width=screenshot_max_width,
                quality=screenshot_quality,
            ),
            SCREENSHOT_DEFINITION,
        )

    if contact_map is not None:
        from ..tools.builtin.contact_mapping import (
            UPDATE_CONTACT_MAPPING_DEFINITION,
            create_update_contact_mapping,
        )

        registry.register(
            "update_contact_mapping",
            create_update_contact_mapping(contact_map),
            UPDATE_CONTACT_MAPPING_DEFINITION,
        )

    return registry, allowed_paths
