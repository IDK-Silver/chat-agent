"""Pre-fetch reviewer: analyzes context and determines what to search before responder."""

import json
import logging
import re

from ..core.schema import AgentConfig
from ..llm.base import LLMClient
from ..llm.schema import Message
from ..tools import ToolRegistry
from .flatten import flatten_for_review
from .schema import PreReviewResult, PrefetchAction

logger = logging.getLogger(__name__)


class PreReviewer:
    """Analyzes conversation context to pre-fetch relevant memory files."""

    def __init__(
        self,
        client: LLMClient,
        system_prompt: str,
        registry: ToolRegistry,
        config: AgentConfig,
    ):
        self.client = client
        self.system_prompt = system_prompt
        self.registry = registry
        self.max_prefetch_actions = config.max_prefetch_actions
        self.max_files_per_grep = config.max_files_per_grep
        self.shell_whitelist = config.shell_whitelist
        self.last_raw_response: str | None = None

    def review(self, messages: list[Message]) -> PreReviewResult | None:
        """Analyze context and determine what to pre-fetch.

        Returns None if review fails or produces no actionable result.
        """
        flat = flatten_for_review(messages)
        review_messages = [
            Message(role="system", content=self.system_prompt),
            *flat,
        ]

        try:
            raw = self.client.chat(review_messages)
            self.last_raw_response = raw
            return self._parse_response(raw)
        except Exception:
            logger.exception("Pre-review failed")
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

    def _parse_response(self, raw: str) -> PreReviewResult | None:
        """Parse JSON from LLM response, handling markdown code blocks."""
        text = raw.strip()
        # Strip markdown code block if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (``` markers)
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
            return PreReviewResult.model_validate(data)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Failed to parse pre-review response: %s", text[:200])
            return None
