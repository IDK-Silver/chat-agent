"""Auto-archive rolling buffer files when they exceed line thresholds."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
import logging
import re

from ..core.schema import MemoryArchiveConfig

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2})")


@dataclass
class ArchivedFile:
    """One date-partition written to the archive directory."""

    date: date
    path: Path
    lines: int


@dataclass
class ArchiveResult:
    """Summary of a single archive run across all buffers."""

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


# -- Buffer specs -------------------------------------------------------------

class _BufferSpec:
    __slots__ = ("rel_path", "archive_subdir", "parse")

    def __init__(
        self,
        rel_path: str,
        archive_subdir: str,
        parse: Callable[[str], dict[date, str]],
    ):
        self.rel_path = rel_path
        self.archive_subdir = archive_subdir
        self.parse = parse


def _parse_short_term_by_date(content: str) -> dict[date, str]:
    """Split short-term.md into sections by `## [YYYY-MM-DD` headers."""
    sections: dict[date, list[str]] = {}
    current_date: date | None = None
    current_lines: list[str] = []

    for line in content.splitlines(keepends=True):
        if line.startswith("## ["):
            m = _DATE_RE.search(line)
            if m:
                # Flush previous section
                if current_date is not None:
                    sections.setdefault(current_date, []).extend(current_lines)
                current_date = date.fromisoformat(m.group(1))
                current_lines = [line]
                continue
        # Lines before any dated header (e.g. `# title`) or continuation
        if current_date is not None:
            current_lines.append(line)
        # Discard preamble lines before any dated section

    # Flush last section
    if current_date is not None:
        sections.setdefault(current_date, []).extend(current_lines)

    return {d: "".join(lines) for d, lines in sections.items()}


def _parse_inner_state_by_date(content: str) -> dict[date, str]:
    """Group inner-state.md lines by `- [YYYY-MM-DD` prefix."""
    groups: dict[date, list[str]] = {}
    current_date: date | None = None

    for line in content.splitlines(keepends=True):
        m = _DATE_RE.search(line)
        if m:
            current_date = date.fromisoformat(m.group(1))
        if current_date is not None:
            groups.setdefault(current_date, []).append(line)

    return {d: "".join(lines) for d, lines in groups.items()}


_BUFFERS = [
    _BufferSpec(
        rel_path="memory/agent/short-term.md",
        archive_subdir="memory/agent/journal/short-term",
        parse=_parse_short_term_by_date,
    ),
    _BufferSpec(
        rel_path="memory/agent/inner-state.md",
        archive_subdir="memory/agent/journal/inner-state",
        parse=_parse_inner_state_by_date,
    ),
]


# -- Archive logic -------------------------------------------------------------

def check_and_archive_buffers(
    working_dir: Path,
    config: MemoryArchiveConfig,
) -> ArchiveResult:
    """Check all rolling buffers; archive entries older than retain_days."""
    today = date.today()
    cutoff = today - timedelta(days=config.retain_days)
    result = ArchiveResult()

    for spec in _BUFFERS:
        buf_path = working_dir / spec.rel_path
        if not buf_path.is_file():
            continue

        content = buf_path.read_text(encoding="utf-8")
        line_count = content.count("\n")
        if line_count <= config.max_lines:
            continue

        dated = spec.parse(content)
        if not dated:
            continue

        old_dates = sorted(d for d in dated if d < cutoff)
        if not old_dates:
            continue

        archive_dir = working_dir / spec.archive_subdir
        archive_dir.mkdir(parents=True, exist_ok=True)

        for d in old_dates:
            archived = _write_archive_file(archive_dir, d, dated[d])
            result.archived.append(archived)

        # Rewrite original with only retained entries
        keep_dates = sorted(d for d in dated if d >= cutoff)
        retained = "".join(dated[d] for d in keep_dates)
        buf_path.write_text(retained, encoding="utf-8")

        _update_archive_index(archive_dir)
        logger.info("Archived %s: %d dates moved", spec.rel_path, len(old_dates))

    return result


def _write_archive_file(archive_dir: Path, d: date, content: str) -> ArchivedFile:
    """Write (or append to) a date-partitioned archive file."""
    path = archive_dir / f"{d.isoformat()}.md"
    lines = content.count("\n")
    if path.exists():
        with path.open("a", encoding="utf-8") as f:
            f.write(content)
    else:
        path.write_text(content, encoding="utf-8")
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
