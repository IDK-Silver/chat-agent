import json

from ..llm.schema import ToolCall


def _extract_memory_paths_from_requests(args: dict) -> tuple[int, list[str]]:
    """Extract request count and unique target paths from memory_edit args."""
    request_list_raw = args.get("requests")
    request_list = (
        [item for item in request_list_raw if isinstance(item, dict)]
        if isinstance(request_list_raw, list)
        else []
    )
    paths: list[str] = []
    seen: set[str] = set()
    for request in request_list:
        path = request.get("target_path")
        if isinstance(path, str) and path and path not in seen:
            seen.add(path)
            paths.append(path)
    return len(request_list), paths


def _format_multiline_paths(paths: list[str]) -> str:
    """Format paths as one item per line without truncation."""
    if not paths:
        return ""
    return "\n".join(f"  - {path}" for path in paths)


def _collect_memory_result_files(payload: dict) -> list[str]:
    """Collect memory_edit result paths with per-file statuses."""
    applied = payload.get("applied")
    if not isinstance(applied, list):
        return []

    pairs: list[str] = []
    seen: set[str] = set()
    for item in applied:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        status = item.get("status")
        if not isinstance(path, str) or not path:
            continue
        if path in seen:
            continue
        seen.add(path)
        if isinstance(status, str) and status:
            pairs.append(f"{path}({status})")
        else:
            pairs.append(path)
    return pairs


def format_tool_call(
    tool_call: ToolCall,
    *,
    gui_intent_max_chars: int | None = None,
) -> str:
    """Format tool call for display."""
    name = tool_call.name
    args = tool_call.arguments

    if name == "read_file":
        return f"Read: {args.get('path', '?')}"
    elif name == "write_file":
        return f"Write: {args.get('path', '?')}"
    elif name == "edit_file":
        return f"Edit: {args.get('path', '?')}"
    elif name == "memory_edit":
        count, paths = _extract_memory_paths_from_requests(args)
        path_summary = _format_multiline_paths(paths)
        if path_summary:
            return f"MemoryEdit: {count} request(s)\n{path_summary}"
        return f"MemoryEdit: {count} request(s)"
    elif name == "execute_shell":
        cmd = args.get("command", "?")
        return f"Shell: {cmd}"
    elif name == "read_image":
        return f"ReadImage: {args.get('path', '?')}"
    elif name == "get_current_time":
        tz = args.get("timezone", "UTC")
        return f"Time: {tz}"
    elif name == "gui_task":
        intent = args.get("intent", "?")
        if gui_intent_max_chars is not None and len(intent) > gui_intent_max_chars:
            intent = intent[:gui_intent_max_chars - 3] + "..."
        app_prompt = args.get("app_prompt", "")
        prompt_info = f"app_prompt: {app_prompt}" if app_prompt else "app_prompt: (none)"
        return f"GUI Task: {intent}\n  {prompt_info}"
    else:
        return f"{name}: {args}"


def format_gui_tool_call(
    tool_call: ToolCall,
    *,
    instruction_max_chars: int = 60,
    text_max_chars: int = 40,
) -> str:
    """Format a GUI manager internal tool call for display."""
    name = tool_call.name
    args = tool_call.arguments

    if name == "ask_worker":
        instruction = args.get("instruction", "?")
        if len(instruction) > instruction_max_chars:
            instruction = instruction[:instruction_max_chars - 3] + "..."
        return f"ask_worker: {instruction}"
    elif name == "click":
        bbox = args.get("bbox", "?")
        return f"click: bbox={bbox}"
    elif name == "type_text":
        text = args.get("text", "?")
        if len(text) > text_max_chars:
            text = text[:text_max_chars - 3] + "..."
        return f'type_text: "{text}"'
    elif name == "key_press":
        return f"key_press: {args.get('key', '?')}"
    elif name == "screenshot":
        return "screenshot"
    elif name == "done":
        return f"done: {args.get('summary', '?')}"
    elif name == "fail":
        return f"fail: {args.get('reason', '?')}"
    elif name == "report_problem":
        return f"report_problem: {args.get('problem', '?')}"
    else:
        return f"{name}: {args}"


def format_gui_tool_result(
    tool_call: ToolCall,
    result: str,
    *,
    worker_result_max_chars: int = 100,
    result_max_chars: int = 60,
) -> str:
    """Format a GUI manager internal tool result for display."""
    name = tool_call.name

    if name == "screenshot":
        return "(screenshot captured)"
    elif name == "ask_worker":
        if len(result) > worker_result_max_chars:
            return result[:worker_result_max_chars - 3] + "..."
        return result
    else:
        if len(result) > result_max_chars:
            return result[:result_max_chars - 3] + "..."
        return result


def format_tool_result(tool_call: ToolCall, result: str) -> str:
    """Format tool result for display."""
    name = tool_call.name

    if result.startswith("Error"):
        # edit_file errors carry actionable hints; show more context.
        if name == "edit_file":
            lines = [line.strip() for line in result.split("\n") if line.strip()]
            excerpt = " | ".join(lines[:3]) if lines else result
            if len(excerpt) > 220:
                excerpt = excerpt[:217] + "..."
            return excerpt

        # memory_edit argument errors often include validation details.
        if name == "memory_edit":
            lines = [line.strip() for line in result.split("\n") if line.strip()]
            excerpt = " | ".join(lines[:6]) if lines else result
            if len(excerpt) > 260:
                excerpt = excerpt[:257] + "..."
            return excerpt

        # Default: show first line only.
        first_line = result.split("\n")[0]
        if len(first_line) > 70:
            first_line = first_line[:67] + "..."
        return first_line

    if name == "read_file":
        if result.startswith("{"):
            try:
                payload = json.loads(result)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and "returned_lines" in payload:
                returned = payload.get("returned_lines", 0)
                total = payload.get("total_lines", "?")
                return f"{returned} lines (json, total={total})"
        lines = result.count("\n") + 1 if result else 0
        return f"{lines} lines"
    elif name == "write_file":
        return result.split("\n")[0]  # "Successfully wrote X bytes..."
    elif name == "edit_file":
        return result.split("\n")[0]  # "Successfully replaced..."
    elif name == "memory_edit":
        if result.startswith("{"):
            try:
                payload = json.loads(result)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                status = payload.get("status", "unknown")
                applied = payload.get("applied", [])
                errors = payload.get("errors", [])
                applied_count = len(applied) if isinstance(applied, list) else 0
                error_count = len(errors) if isinstance(errors, list) else 0
                file_items = _collect_memory_result_files(payload)
                file_summary = _format_multiline_paths(file_items)
                if status == "failed" and isinstance(errors, list) and errors:
                    first = errors[0]
                    if isinstance(first, dict):
                        code = first.get("code", "unknown")
                        detail = first.get("detail", "")
                        base = (
                            f"failed ({code}): {detail}"
                            if detail
                            else f"failed ({code})"
                        )
                        if file_summary:
                            return f"{base}\nfiles:\n{file_summary}"
                        return base
                base = f"status={status}, applied={applied_count}, errors={error_count}"
                if file_summary:
                    return f"{base}\nfiles:\n{file_summary}"
                return base
        return result.split("\n")[0]
    elif name == "execute_shell":
        stripped = result.strip()
        if not stripped:
            return "(empty)"
        return stripped
    elif name == "read_image":
        # Show just the metadata line
        first_line = result.split("\n")[0]
        if len(first_line) > 80:
            first_line = first_line[:77] + "..."
        return first_line
    elif name == "get_current_time":
        return result
    else:
        if len(result) > 70:
            return result[:67] + "..."
        return result
