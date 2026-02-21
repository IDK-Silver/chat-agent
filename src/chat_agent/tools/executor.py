"""Shell command executor with safety controls."""

import os
import re
import signal
import subprocess
from pathlib import Path

from dotenv import dotenv_values

# Output truncation limit (100KB)
MAX_OUTPUT_SIZE = 100 * 1024

# Marker for extracting cwd after command execution
_CWD_MARKER = "__CWD_MARKER_8f3a2b__"


def _load_env_allowlist(keys: list[str]) -> dict[str, str]:
    """Load specific keys from .env file. Ignores missing keys."""
    if not keys:
        return {}
    all_values = dotenv_values()
    return {k: all_values[k] for k in keys if k in all_values}


class ShellExecutor:
    """Execute shell commands with cwd tracking and safety controls."""

    def __init__(
        self,
        agent_os_dir: Path,
        blacklist: list[str] | None = None,
        timeout: int = 30,
        export_env: list[str] | None = None,
    ):
        """Initialize the executor.

        Args:
            agent_os_dir: Initial working directory.
            blacklist: List of regex patterns to block.
            timeout: Command timeout in seconds.
            export_env: Keys to load from .env into subprocess environment.
        """
        self._cwd = agent_os_dir.resolve()
        self._blacklist = [re.compile(p) for p in (blacklist or [])]
        self._timeout = timeout
        self._extra_env = _load_env_allowlist(export_env or [])

        # Ensure working directory exists
        self._cwd.mkdir(parents=True, exist_ok=True)

    @property
    def cwd(self) -> Path:
        """Current working directory."""
        return self._cwd

    def is_blocked(self, command: str) -> str | None:
        """Check if command matches any blacklist pattern.

        Returns:
            The matched pattern string if blocked, None otherwise.
        """
        for pattern in self._blacklist:
            if pattern.search(command):
                return pattern.pattern
        return None

    def execute(self, command: str, timeout: int | None = None) -> str:
        """Execute a shell command and return output.

        Args:
            command: The shell command to execute.
            timeout: Override timeout in seconds (uses default if None).

        Returns:
            Command output (stdout + stderr) or error message.
        """
        # Check blacklist
        blocked = self.is_blocked(command)
        if blocked:
            return f"Error: Command blocked by pattern '{blocked}'"

        # Append pwd to track directory changes
        # Use newlines instead of semicolons to avoid breaking heredocs
        full_command = f"{command}\necho '{_CWD_MARKER}'\npwd"

        # Use provided timeout or fall back to default; clamp to configured minimum
        effective_timeout = timeout if timeout is not None else self._timeout
        if effective_timeout < self._timeout:
            effective_timeout = self._timeout

        try:
            env = {**os.environ, **self._extra_env} if self._extra_env else None
            process = subprocess.Popen(
                full_command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(self._cwd),
                env=env,
                text=True,
                # Create new process group for proper cleanup
                preexec_fn=os.setsid,
            )

            try:
                output, _ = process.communicate(timeout=effective_timeout)
            except subprocess.TimeoutExpired:
                # Kill the entire process group
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                process.wait()
                return f"Error: Command timed out after {effective_timeout} seconds"

            # Extract new cwd from output
            if _CWD_MARKER in output:
                parts = output.rsplit(_CWD_MARKER, 1)
                output = parts[0].rstrip()
                # Take only the last line (pwd output), ignore any extra output
                pwd_output = parts[1].strip()
                new_cwd = pwd_output.splitlines()[-1] if pwd_output else ""
                if new_cwd and new_cwd.startswith("/"):
                    new_cwd_path = Path(new_cwd).resolve()
                    if new_cwd_path.exists() and new_cwd_path.is_dir():
                        self._cwd = new_cwd_path

            # Truncate if too large
            if len(output) > MAX_OUTPUT_SIZE:
                output = output[:MAX_OUTPUT_SIZE] + "\n... (output truncated)"

            return output

        except Exception as e:
            return f"Error: {e}"
