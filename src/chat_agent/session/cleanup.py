"""Shared session cleanup for brain and GUI sessions."""

import logging
import shutil
from datetime import datetime, timedelta, timezone as tz
from pathlib import Path

logger = logging.getLogger(__name__)

# Session ID prefix format: YYYYMMDD_HHMMSS
_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
_TIMESTAMP_PREFIX_LEN = 15  # len("20260215_120000")


def _parse_session_timestamp(name: str) -> datetime | None:
    """Extract creation timestamp from a session ID / filename."""
    prefix = name[:_TIMESTAMP_PREFIX_LEN]
    try:
        return datetime.strptime(prefix, _TIMESTAMP_FORMAT).replace(tzinfo=tz.utc)
    except (ValueError, IndexError):
        return None


def cleanup_sessions(
    session_base_dir: Path,
    retention_days: int = 30,
) -> int:
    """Remove expired sessions under session/brain/ and session/gui/.

    Returns the number of deleted entries.
    """
    cutoff = datetime.now(tz.utc) - timedelta(days=retention_days)
    deleted = 0

    # Brain sessions: each session is a directory
    brain_dir = session_base_dir / "brain"
    if brain_dir.is_dir():
        for entry in brain_dir.iterdir():
            if not entry.is_dir():
                continue
            ts = _parse_session_timestamp(entry.name)
            if ts is None:
                continue
            if ts < cutoff:
                try:
                    shutil.rmtree(entry)
                    deleted += 1
                except OSError:
                    logger.warning("Failed to remove brain session: %s", entry)

    # GUI sessions: each session is a .json file
    gui_dir = session_base_dir / "gui"
    if gui_dir.is_dir():
        for entry in gui_dir.iterdir():
            if not entry.is_file() or entry.suffix != ".json":
                continue
            ts = _parse_session_timestamp(entry.stem)
            if ts is None:
                continue
            if ts < cutoff:
                try:
                    entry.unlink()
                    deleted += 1
                except OSError:
                    logger.warning("Failed to remove GUI session: %s", entry)

    return deleted
