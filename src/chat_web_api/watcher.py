"""File watcher: detect JSONL changes and trigger cache refresh + WebSocket broadcast."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from watchfiles import awatch

from .cache import MetricsCache

logger = logging.getLogger(__name__)


def _extract_session_id(path_str: str, sessions_dir: Path) -> str | None:
    """Extract session_id from a changed file path."""
    try:
        rel = Path(path_str).relative_to(sessions_dir)
    except ValueError:
        return None
    parts = rel.parts
    if not parts:
        return None
    return parts[0]


async def watch_sessions(
    sessions_dir: Path,
    cache: MetricsCache,
    broadcast: Callable[[dict], Awaitable[None]],
    stop_event: asyncio.Event,
) -> None:
    """Watch session directory for JSONL changes; refresh cache and broadcast."""
    if not sessions_dir.exists():
        logger.warning("Sessions directory does not exist: %s", sessions_dir)
        return

    logger.info("Watching sessions directory: %s", sessions_dir)
    known_sessions = set(cache._files.keys())

    async for changes in awatch(sessions_dir, stop_event=stop_event):
        affected: set[str] = set()
        for _change_type, path_str in changes:
            sid = _extract_session_id(path_str, sessions_dir)
            if sid:
                affected.add(sid)

        for sid in affected:
            try:
                changed = cache.refresh_session(sid)
            except Exception:
                logger.warning("Error refreshing session %s", sid, exc_info=True)
                continue

            if changed:
                if sid not in known_sessions:
                    known_sessions.add(sid)
                    await broadcast({"type": "session_created", "session_id": sid})
                else:
                    await broadcast({"type": "session_updated", "session_id": sid})

                # Push live token update if this is the active session
                live = cache.get_live_status(
                    soft_limit=128_000  # will be overridden by app
                )
                if live and live["session_id"] == sid:
                    await broadcast(
                        {
                            "type": "live_token_update",
                            "session_id": sid,
                            "prompt_tokens": live["prompt_tokens"],
                            "soft_limit": live["soft_limit"],
                            "hard_limit": live["hard_limit"],
                        }
                    )
