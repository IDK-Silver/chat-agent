"""Auto-archive recent.md rolling buffer entries older than retain_days."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
import logging
import re

from ..core.schema import MemoryArchiveConfig
from ..timezone_utils import now as tz_now

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2})")

_RECENT_REL_PATH = "memory/agent/recent.md"
_RECENT_ARCHIVE_SUBDIR = "memory/agent/journal/recent"


@dataclass
class ArchivedFile:
    """One date-partition written to the archive directory."""

    date: date
    path: Path
    lines: int


@dataclass
class ArchiveResult:
    """Summary of a single archive run."""

    archived: list[ArchivedFile] = field(default_factory=list)

    @property
    def total_lines(self) -> int:
        return sum(f.lines for f in self.archived)

    @property
    def summary(self) -> str:
        if not self.archived:
            return ""
        dates = sorted({f.date for f in self.archived})
        return f"{self.total_lines} lines archived ({dates[0]} ~ {dates[-1]})"


# -- Parser -------------------------------------------------------------------

def _parse_recent_by_date(content: str) -> tuple[str, dict[date, str]]:
    """Parse recent.md into preamble + date-grouped entries.

    Returns (preamble, {date: content}).
    Preamble = everything before the first dated entry (title, blank lines).
    """
    preamble_lines: list[str] = []
    groups: dict[date, list[str]] = {}
    current_date: date | None = None

    for line in content.splitlines(keepends=True):
        m = _DATE_RE.search(line)
        if m:
            current_date = date.fromisoformat(m.group(1))

        if current_date is None:
            preamble_lines.append(line)
        else:
            groups.setdefault(current_date, []).append(line)

    preamble = "".join(preamble_lines)
    return preamble, {d: "".join(lines) for d, lines in groups.items()}


# -- Archive logic -------------------------------------------------------------

def check_and_archive_buffers(
    agent_os_dir: Path,
    config: MemoryArchiveConfig,
) -> ArchiveResult:
    """Archive recent.md entries older than retain_days."""
    buf_path = agent_os_dir / _RECENT_REL_PATH
    result = ArchiveResult()

    if not buf_path.is_file():
        return result

    content = buf_path.read_text(encoding="utf-8")
    preamble, dated = _parse_recent_by_date(content)
    if not dated:
        return result

    today = tz_now().date()
    cutoff = today - timedelta(days=config.retain_days)
    old_dates = sorted(d for d in dated if d < cutoff)
    if not old_dates:
        return result

    archive_dir = agent_os_dir / _RECENT_ARCHIVE_SUBDIR
    archive_dir.mkdir(parents=True, exist_ok=True)

    for d in old_dates:
        archived = _write_archive_file(archive_dir, d, dated[d])
        result.archived.append(archived)

    # Rewrite with preamble preserved
    keep_dates = sorted(d for d in dated if d >= cutoff)
    retained = preamble + "".join(dated[d] for d in keep_dates)
    buf_path.write_text(retained, encoding="utf-8")

    _update_archive_index(archive_dir)
    logger.info("Archived %s: %d dates moved", _RECENT_REL_PATH, len(old_dates))

    return result


def _write_archive_file(archive_dir: Path, d: date, content: str) -> ArchivedFile:
    """Write (or append to) a date-partitioned archive file."""
    path = archive_dir / f"{d.isoformat()}.md"
    lines = content.count("\n")
    if path.exists():
        with path.open("a", encoding="utf-8") as f:
            f.write(content)
    else:
        # New archive file gets a date title header
        path.write_text(f"# {d.isoformat()}\n\n{content}", encoding="utf-8")
    return ArchivedFile(date=d, path=path, lines=lines)


def _update_archive_index(archive_dir: Path) -> None:
    """Rebuild index.md listing all date files in the archive directory."""
    md_files = sorted(
        f for f in archive_dir.iterdir()
        if f.suffix == ".md" and f.name != "index.md"
    )
    lines = [f"# {archive_dir.name} archive\n", "\n"]
    for f in md_files:
        lines.append(f"- [{f.stem}]({f.name})\n")
    (archive_dir / "index.md").write_text("".join(lines), encoding="utf-8")
