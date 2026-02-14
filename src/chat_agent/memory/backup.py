"""Periodic memory directory backup with zip compression and auto-cleanup."""

from datetime import datetime, timedelta
from pathlib import Path
import logging
import zipfile

from ..core.schema import MemoryBackupConfig

logger = logging.getLogger(__name__)

_FILENAME_FORMAT = "memory_%Y%m%d_%H%M%S.zip"
_FILENAME_TS_SLICE = slice(7, 22)  # "memory_YYYYMMDD_HHMMSS.zip" -> "YYYYMMDD_HHMMSS"


class MemoryBackupManager:
    """Creates periodic zip backups of the memory directory."""

    def __init__(self, working_dir: Path, config: MemoryBackupConfig):
        self._memory_dir = working_dir / "memory"
        self._backup_dir = working_dir / "backups" / "memory"
        self._interval = timedelta(minutes=config.interval_minutes)
        self._retention = timedelta(minutes=config.retention_minutes)
        self._last_backup: datetime | None = None

    def check_and_backup(self) -> Path | None:
        """If interval elapsed, create zip backup and cleanup expired.

        Returns the backup path if created, None otherwise.
        """
        if not self._memory_dir.is_dir():
            return None

        now = datetime.now()
        if self._last_backup is not None and (now - self._last_backup) < self._interval:
            return None

        path = self._create_backup(now)
        self._cleanup_expired(now)
        return path

    def _create_backup(self, now: datetime) -> Path:
        """Zip all files under memory/ into backups/memory/memory_YYYYMMDD_HHMMSS.zip."""
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        filename = now.strftime(_FILENAME_FORMAT)
        backup_path = self._backup_dir / filename

        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(self._memory_dir.rglob("*")):
                if file_path.is_file():
                    arcname = file_path.relative_to(self._memory_dir)
                    zf.write(file_path, arcname)

        self._last_backup = now
        logger.info("Memory backup created: %s", filename)
        return backup_path

    def _cleanup_expired(self, now: datetime) -> int:
        """Delete zip files older than retention period. Returns count deleted."""
        if not self._backup_dir.is_dir():
            return 0

        cutoff = now - self._retention
        deleted = 0
        for path in self._backup_dir.iterdir():
            if not path.name.endswith(".zip"):
                continue
            ts = _parse_filename_timestamp(path.name)
            if ts is not None and ts < cutoff:
                path.unlink()
                deleted += 1
                logger.debug("Deleted expired backup: %s", path.name)
        return deleted


def _parse_filename_timestamp(filename: str) -> datetime | None:
    """Extract datetime from 'memory_YYYYMMDD_HHMMSS.zip' filename."""
    try:
        ts_str = filename[_FILENAME_TS_SLICE]
        return datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
    except (ValueError, IndexError):
        return None
