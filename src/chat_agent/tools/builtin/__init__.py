"""Built-in tools for the agent."""

from .time import get_current_time, GET_CURRENT_TIME_DEFINITION
from .shell import (
    EXECUTE_SHELL_DEFINITION,
    create_execute_shell,
    is_claude_code_stream_json_command,
)
from .shell_task import SHELL_TASK_DEFINITION, create_shell_task
from .web_search import WEB_SEARCH_DEFINITION, create_web_search
from .file import (
    READ_FILE_DEFINITION,
    WRITE_FILE_DEFINITION,
    EDIT_FILE_DEFINITION,
    create_read_file,
    create_write_file,
    create_edit_file,
)
from .image import (
    READ_IMAGE_DEFINITION,
    READ_IMAGE_BY_SUBAGENT_DEFINITION,
    create_read_image_vision,
    create_read_image_with_sub_agent,
    create_read_image_by_subagent,
)
from .vision import VisionAgent

__all__ = [
    "get_current_time",
    "GET_CURRENT_TIME_DEFINITION",
    "EXECUTE_SHELL_DEFINITION",
    "create_execute_shell",
    "is_claude_code_stream_json_command",
    "SHELL_TASK_DEFINITION",
    "create_shell_task",
    "WEB_SEARCH_DEFINITION",
    "create_web_search",
    "READ_FILE_DEFINITION",
    "WRITE_FILE_DEFINITION",
    "EDIT_FILE_DEFINITION",
    "create_read_file",
    "create_write_file",
    "create_edit_file",
    "READ_IMAGE_DEFINITION",
    "READ_IMAGE_BY_SUBAGENT_DEFINITION",
    "create_read_image_vision",
    "create_read_image_with_sub_agent",
    "create_read_image_by_subagent",
    "VisionAgent",
]
