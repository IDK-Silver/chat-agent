"""Pre-fetch reviewer: analyzes context and determines what to search before responder."""

import logging
import re
from pathlib import PurePosixPath

from ..core.schema import AgentConfig
from ..llm.base import LLMClient
from ..llm.schema import Message
from ..tools import ToolRegistry
from .json_extract import extract_json_object
from .flatten import flatten_for_review
from .schema import PreReviewResult, PrefetchAction

logger = logging.getLogger(__name__)

_DEFAULT_PARSE_RETRY_PROMPT = (
    "Your previous output was invalid.\n"
    "Return ONLY a JSON object with keys: triggered_rules, prefetch, reminders.\n"
    "Do not include markdown fences, explanations, or tool-call text."
)

_MEMORY_PATH_REMAP = {
    "memory/knowledge/": "memory/agent/knowledge/",
    "memory/thoughts/": "memory/agent/thoughts/",
    "memory/experiences/": "memory/agent/experiences/",
    "memory/skills/": "memory/agent/skills/",
    "memory/interests/": "memory/agent/interests/",
    "memory/journal/": "memory/agent/journal/",
}

_ALLOWED_MEMORY_ROOTS = [
    "memory/short-term.md",
    "memory/people/",
    "memory/agent/index.md",
    "memory/agent/persona.md",
    "memory/agent/config.md",
    "memory/agent/protocol.md",
    "memory/agent/inner-state.md",
    "memory/agent/pending-thoughts.md",
    "memory/agent/knowledge/",
    "memory/agent/thoughts/",
    "memory/agent/experiences/",
    "memory/agent/skills/",
    "memory/agent/interests/",
    "memory/agent/journal/",
]


class PreReviewer:
    """Analyzes conversation context to pre-fetch relevant memory files."""

    def __init__(
        self,
        client: LLMClient,
        system_prompt: str,
        registry: ToolRegistry,
        config: AgentConfig,
        parse_retry_prompt: str | None = None,
    ):
        self.client = client
        self.system_prompt = system_prompt
        self.registry = registry
        self.max_prefetch_actions = config.max_prefetch_actions
        self.max_files_per_grep = config.max_files_per_grep
        self.shell_whitelist = config.shell_whitelist
        self.pre_parse_retries = config.pre_parse_retries
        self.enforce_memory_path_constraints = config.enforce_memory_path_constraints
        self.parse_retry_prompt = parse_retry_prompt or _DEFAULT_PARSE_RETRY_PROMPT
        self.last_raw_response: str | None = None

    def review(self, messages: list[Message]) -> PreReviewResult | None:
        """Analyze context and determine what to pre-fetch.

        Returns None if review fails or produces no actionable result.
        """
        flat = flatten_for_review(messages)
        base_messages = [
            Message(role="system", content=self.system_prompt),
            *flat,
        ]
        review_messages = base_messages

        try:
            for attempt in range(self.pre_parse_retries + 1):
                raw = self.client.chat(review_messages)
                self.last_raw_response = raw
                is_final_attempt = attempt >= self.pre_parse_retries
                result = self._parse_response(
                    raw,
                    final_attempt=is_final_attempt,
                )
                if result is not None:
                    return self._sanitize_result(result)
                if attempt < self.pre_parse_retries:
                    review_messages = [
                        *base_messages,
                        Message(role="user", content=self.parse_retry_prompt),
                    ]
            return None
        except Exception as e:
            logger.warning("Pre-review failed: %s", e)
            self.last_raw_response = None
            return None

    def execute_prefetch(self, result: PreReviewResult) -> list[str]:
        """Execute prefetch actions and return results as strings.

        Supports recursive expansion: grep results are parsed for file paths,
        and matching files are automatically read.
        """
        outputs: list[str] = []
        actions = result.prefetch[: self.max_prefetch_actions]

        for action in actions:
            try:
                output = self._execute_action(action)
                if output is None:
                    continue
                outputs.append(f"### {action.reason}\n{output}")

                # Recursive expansion for grep commands
                if action.tool == "execute_shell" and self._is_grep_command(
                    action.arguments.get("command", "")
                ):
                    expanded = self._expand_grep_results(output)
                    outputs.extend(expanded)
            except Exception:
                logger.exception("Prefetch action failed: %s", action.reason)

        return outputs

    def _execute_action(self, action: PrefetchAction) -> str | None:
        """Execute a single prefetch action with safety checks."""
        action = self._sanitize_action(action)
        if action is None:
            return None

        if action.tool == "execute_shell":
            cmd = action.arguments.get("command", "")
            if not self._is_allowed_command(cmd):
                logger.warning("Blocked shell command: %s", cmd)
                return None

        if not self.registry.has_tool(action.tool):
            return None

        from ..llm.schema import ToolCall

        tool_call = ToolCall(
            id="prefetch",
            name=action.tool,
            arguments=action.arguments,
        )
        return self.registry.execute(tool_call)

    def _is_allowed_command(self, command: str) -> bool:
        """Check if a shell command starts with a whitelisted binary."""
        cmd = command.strip()
        first_word = cmd.split()[0] if cmd else ""
        return first_word in self.shell_whitelist

    def _is_grep_command(self, command: str) -> bool:
        """Check if a command is a grep command."""
        return command.strip().startswith("grep")

    def _expand_grep_results(self, grep_output: str) -> list[str]:
        """Parse grep output for file paths and read matching files."""
        # grep format: filepath:line:content
        file_paths: list[str] = []
        seen: set[str] = set()

        for line in grep_output.splitlines():
            match = re.match(r"^([^:]+):\d+:", line)
            if match:
                path = match.group(1)
                if path not in seen:
                    seen.add(path)
                    file_paths.append(path)

        results: list[str] = []
        for path in file_paths[: self.max_files_per_grep]:
            if not self.registry.has_tool("read_file"):
                break
            from ..llm.schema import ToolCall

            tool_call = ToolCall(
                id="prefetch-expand",
                name="read_file",
                arguments={"path": path},
            )
            content = self.registry.execute(tool_call)
            if not content.startswith("Error"):
                results.append(f"### [Auto-loaded] {path}\n{content}")

        return results

    def _parse_response(
        self,
        raw: str,
        *,
        final_attempt: bool,
    ) -> PreReviewResult | None:
        """Parse JSON from LLM response, handling mixed reasoning output."""
        data = extract_json_object(raw)
        if data is None:
            log = logger.warning if final_attempt else logger.debug
            log("Failed to parse pre-review response: %s", raw.strip()[:200])
            return None
        try:
            return PreReviewResult.model_validate(data)
        except ValueError:
            log = logger.warning if final_attempt else logger.debug
            log("Invalid pre-review schema: %s", str(data)[:200])
            return None

    def _sanitize_result(self, result: PreReviewResult) -> PreReviewResult:
        """Normalize and constrain prefetch actions for safe execution."""
        sanitized_prefetch: list[PrefetchAction] = []
        for action in result.prefetch:
            sanitized = self._sanitize_action(action)
            if sanitized is not None:
                sanitized_prefetch.append(sanitized)
        return PreReviewResult(
            triggered_rules=result.triggered_rules,
            prefetch=sanitized_prefetch,
            reminders=result.reminders,
        )

    def _sanitize_action(self, action: PrefetchAction) -> PrefetchAction | None:
        """Apply hard constraints and path normalization to a prefetch action."""
        arguments = dict(action.arguments)

        if action.tool == "read_file":
            path = self._normalize_memory_path(arguments.get("path", ""))
            if path is None:
                return None
            arguments["path"] = path
            return PrefetchAction(tool=action.tool, arguments=arguments, reason=action.reason)

        if action.tool == "execute_shell":
            command = self._normalize_command_paths(arguments.get("command", ""))
            if self.enforce_memory_path_constraints and not self._is_command_path_safe(command):
                logger.warning("Blocked shell command by path constraint: %s", command)
                return None
            arguments["command"] = command
            return PrefetchAction(tool=action.tool, arguments=arguments, reason=action.reason)

        return PrefetchAction(tool=action.tool, arguments=arguments, reason=action.reason)

    def _normalize_memory_path(self, path: str) -> str | None:
        """Normalize memory path and enforce allowed roots."""
        normalized = (path or "").strip().replace("\\", "/")
        if not normalized:
            return None
        if normalized.startswith(".agent/memory/"):
            normalized = "memory/" + normalized[len(".agent/memory/") :]
        if normalized.startswith("./"):
            normalized = normalized[2:]
        for wrong_prefix, right_prefix in _MEMORY_PATH_REMAP.items():
            if normalized.startswith(wrong_prefix):
                normalized = right_prefix + normalized[len(wrong_prefix) :]

        posix_path = str(PurePosixPath(normalized))
        if not posix_path.startswith("memory/"):
            return None
        if "/../" in f"/{posix_path}/":
            return None

        if self.enforce_memory_path_constraints and not self._is_allowed_memory_path(posix_path):
            logger.warning("Blocked read_file path by constraint: %s", posix_path)
            return None
        return posix_path

    def _normalize_command_paths(self, command: str) -> str:
        """Normalize known path prefixes inside shell commands."""
        normalized = command.replace(".agent/memory/", "memory/")
        for wrong_prefix, right_prefix in _MEMORY_PATH_REMAP.items():
            normalized = normalized.replace(wrong_prefix, right_prefix)
        return normalized

    def _is_allowed_memory_path(self, path: str) -> bool:
        """Check if path falls under configured memory roots."""
        for root in _ALLOWED_MEMORY_ROOTS:
            if root.endswith("/"):
                if path.startswith(root):
                    return True
            elif path == root:
                return True
        return False

    def _is_command_path_safe(self, command: str) -> bool:
        """Reject commands that reference disallowed memory roots."""
        if ".agent/memory/" in command:
            return False
        return all(
            bad not in command
            for bad in _MEMORY_PATH_REMAP.keys()
        )
