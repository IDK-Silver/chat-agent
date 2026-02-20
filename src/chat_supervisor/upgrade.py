"""Upgrade logic: git fetch/pull, post_pull, self-restart detection."""

import logging
import os
import subprocess
import sys
from pathlib import Path

from .schema import UpgradeConfig

logger = logging.getLogger(__name__)


def has_remote_changes(branch: str) -> bool:
    """Check if the remote branch has new commits ahead of local."""
    result = subprocess.run(
        ["git", "fetch", "origin", branch],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.warning("git fetch failed: %s", result.stderr.strip())
        return False

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    local_head = result.stdout.strip()

    result = subprocess.run(
        ["git", "rev-parse", f"origin/{branch}"],
        capture_output=True, text=True,
    )
    remote_head = result.stdout.strip()

    if local_head != remote_head:
        logger.info(
            "Remote has changes: local=%s remote=%s",
            local_head[:8], remote_head[:8],
        )
        return True
    return False


def pull_and_post(config: UpgradeConfig) -> tuple[bool, str]:
    """Run git pull + post_pull commands.

    Returns (success, error_message).
    """
    result = subprocess.run(
        ["git", "pull"], capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False, f"git pull failed: {result.stderr.strip()}"

    if config.post_pull:
        result = subprocess.run(
            config.post_pull, capture_output=True, text=True,
        )
        if result.returncode != 0:
            return False, f"post_pull failed: {result.stderr.strip()}"

    return True, ""


def snapshot_watch_paths(paths: list[str]) -> dict[str, float]:
    """Collect mtime of watched paths for change detection."""
    result: dict[str, float] = {}
    for p in paths:
        path = Path(p)
        if path.is_dir():
            for f in path.rglob("*.py"):
                result[str(f)] = f.stat().st_mtime
        elif path.is_file():
            result[str(path)] = path.stat().st_mtime
    return result


def self_restart() -> None:
    """Replace the current process with a fresh supervisor."""
    logger.info("Self-restarting supervisor via os.execv")
    os.execv(sys.executable, [sys.executable, "-m", "chat_supervisor"])
