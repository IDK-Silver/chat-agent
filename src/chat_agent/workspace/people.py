"""People memory utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import re
from pathlib import Path


USER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_PEOPLE_TABLE_HEADER = "| user_id | display_name | aliases | last_seen |"


@dataclass(frozen=True)
class PersonEntry:
    user_id: str
    display_name: str
    aliases: tuple[str, ...] = ()
    last_seen: str | None = None  # YYYY-MM-DD


def normalize_user_id(user_id: str) -> str:
    """Normalize and validate a user_id."""
    normalized = user_id.strip().lower()
    if not USER_ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"Invalid user_id: {user_id!r}")
    return normalized


def _hash_user_id(seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
    return f"u-{digest}"


def _first_markdown_heading(path: Path) -> str | None:
    if not path.is_file():
        return None

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            heading = line[2:].strip()
            return heading or None
    return None


def _extract_basic_info_display_name(path: Path) -> str | None:
    if not path.is_file():
        return None

    lines = path.read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(lines):
        if line.strip() != "## Display Name":
            continue
        for candidate in lines[idx + 1 :]:
            value = candidate.strip()
            if value:
                return value
        break
    return None


def _has_person_memory_files(user_dir: Path) -> bool:
    if not user_dir.is_dir():
        return False
    return any(
        child.is_file() and child.suffix == ".md"
        for child in user_dir.iterdir()
    )


def infer_person_display_name(memory_dir: Path, user_id: str) -> str | None:
    """Infer a display name for an existing people/<user_id>/ directory."""
    normalized_id = normalize_user_id(user_id)
    user_dir = memory_dir / "people" / normalized_id

    heading = _first_markdown_heading(user_dir / "index.md")
    if heading and heading.casefold() != "index":
        return heading

    display_name = _extract_basic_info_display_name(user_dir / "basic-info.md")
    if display_name:
        return display_name

    heading = _first_markdown_heading(user_dir / "basic-info.md")
    if heading and heading.casefold() != "user memory":
        return heading

    return None


def generate_user_id(display_name: str) -> str:
    """Generate a safe user_id from a display name (deterministic)."""
    raw = display_name.strip().lower()
    if not raw:
        return _hash_user_id(display_name)

    cleaned = []
    for ch in raw:
        if "a" <= ch <= "z" or "0" <= ch <= "9" or ch in ("_", "-"):
            cleaned.append(ch)
        elif ch.isspace():
            cleaned.append("-")
        else:
            cleaned.append("-")

    candidate = re.sub(r"-{2,}", "-", "".join(cleaned)).strip("-_")
    if not candidate:
        return _hash_user_id(display_name)

    if not ("a" <= candidate[0] <= "z"):
        candidate = f"u-{candidate}"

    candidate = candidate[:32].rstrip("-_")
    if USER_ID_PATTERN.fullmatch(candidate):
        return candidate

    return _hash_user_id(display_name)


def _parse_people_table(lines: list[str]) -> list[PersonEntry]:
    header_index = None
    for idx, line in enumerate(lines):
        if line.strip().lower() == _PEOPLE_TABLE_HEADER:
            header_index = idx
            break

    if header_index is None:
        return []

    entries: list[PersonEntry] = []
    for line in lines[header_index + 2 :]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break

        parts = [p.strip() for p in stripped.strip("|").split("|")]
        if len(parts) < 4:
            continue

        user_id_raw, display_name, aliases_raw, last_seen = parts[:4]
        user_id = user_id_raw.strip()
        if not user_id or not display_name:
            continue

        if not USER_ID_PATTERN.fullmatch(user_id):
            continue

        aliases = tuple(a.strip() for a in aliases_raw.split(",") if a.strip())
        last_seen_value = last_seen.strip() or None
        entries.append(
            PersonEntry(
                user_id=user_id,
                display_name=display_name.strip(),
                aliases=aliases,
                last_seen=last_seen_value,
            )
        )

    return entries


def load_people_index(index_path: Path) -> tuple[list[PersonEntry], str | None]:
    """Load people/index.md entries and return legacy content if present."""
    if not index_path.exists():
        return [], None

    content = index_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    entries = _parse_people_table(lines)
    has_table_header = any(line.strip().lower() == _PEOPLE_TABLE_HEADER for line in lines)

    if entries or has_table_header:
        return entries, None

    legacy = content.strip()
    return [], legacy if legacy else None


def save_people_index(index_path: Path, entries: list[PersonEntry], legacy: str | None) -> None:
    """Save people/index.md in a stable, parseable format."""
    header = [
        "# People Index",
        "",
        "This file maps human names to stable user_id identifiers.",
        "",
        "## Naming Convention",
        "",
        "Each user has a folder: `{user_id}/basic-info.md`",
        "",
        "## People",
        "",
        _PEOPLE_TABLE_HEADER,
        "|---------|--------------|---------|-----------|",
    ]

    rows = []
    for entry in sorted(entries, key=lambda e: (e.display_name.lower(), e.user_id)):
        aliases = ", ".join(entry.aliases)
        last_seen = entry.last_seen or ""
        rows.append(f"| {entry.user_id} | {entry.display_name} | {aliases} | {last_seen} |")

    lines = header + rows
    if legacy:
        lines += [
            "",
            "## Legacy",
            "",
            legacy,
        ]

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def upsert_person_entry(
    entries: list[PersonEntry],
    user_id: str,
    display_name: str,
    *,
    seen_date: str,
) -> list[PersonEntry]:
    """Insert or update a person entry."""
    normalized_id = normalize_user_id(user_id)
    updated: list[PersonEntry] = []
    found = False

    for entry in entries:
        if entry.user_id != normalized_id:
            updated.append(entry)
            continue

        found = True
        updated.append(
            PersonEntry(
                user_id=entry.user_id,
                display_name=display_name.strip() or entry.display_name,
                aliases=entry.aliases,
                last_seen=seen_date,
            )
        )

    if not found:
        updated.append(
            PersonEntry(
                user_id=normalized_id,
                display_name=display_name.strip() or normalized_id,
                aliases=(),
                last_seen=seen_date,
            )
        )

    return updated


def remove_person_entry(entries: list[PersonEntry], user_id: str) -> list[PersonEntry]:
    """Remove one person entry by user_id."""
    normalized_id = normalize_user_id(user_id)
    return [entry for entry in entries if entry.user_id != normalized_id]


def sync_people_index_entry(memory_dir: Path, user_id: str, *, seen_date: str | None = None) -> None:
    """Upsert or remove one people/index.md entry to match filesystem state.

    If ``memory/people/<user_id>/`` has no markdown files, the entry is removed.
    Otherwise the entry is upserted and ``last_seen`` is updated.
    """
    normalized_id = normalize_user_id(user_id)
    people_dir = memory_dir / "people"
    index_path = people_dir / "index.md"
    entries, legacy = load_people_index(index_path)
    user_dir = people_dir / normalized_id

    if not _has_person_memory_files(user_dir):
        updated = remove_person_entry(entries, normalized_id)
        if len(updated) != len(entries):
            save_people_index(index_path, updated, legacy)
        return

    existing = next((e for e in entries if e.user_id == normalized_id), None)
    display_name = (
        infer_person_display_name(memory_dir, normalized_id)
        or (existing.display_name if existing is not None else "")
        or normalized_id
    )
    effective_seen = seen_date or (existing.last_seen if existing is not None else None) or date.today().isoformat()
    updated = upsert_person_entry(entries, normalized_id, display_name, seen_date=effective_seen)
    save_people_index(index_path, updated, legacy)


def resolve_user_selector(memory_dir: Path, user_selector: str) -> tuple[str, str]:
    """Resolve user selector input to a stable (user_id, display_name)."""
    raw = user_selector.strip()
    if not raw:
        raise ValueError("user is required")

    people_dir = memory_dir / "people"
    index_path = people_dir / "index.md"

    entries, legacy = load_people_index(index_path)

    candidate_id = raw.lower()
    if USER_ID_PATTERN.fullmatch(candidate_id):
        display_name = next(
            (e.display_name for e in entries if e.user_id == candidate_id),
            raw,
        )
        seen_date = date.today().isoformat()
        updated = upsert_person_entry(entries, candidate_id, display_name, seen_date=seen_date)
        save_people_index(index_path, updated, legacy)
        return candidate_id, display_name

    matches = [
        e
        for e in entries
        if e.display_name.casefold() == raw.casefold()
        or any(a.casefold() == raw.casefold() for a in e.aliases)
    ]
    if len(matches) == 1:
        seen_date = date.today().isoformat()
        updated = upsert_person_entry(
            entries,
            matches[0].user_id,
            matches[0].display_name,
            seen_date=seen_date,
        )
        save_people_index(index_path, updated, legacy)
        return matches[0].user_id, matches[0].display_name

    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous user selector {user_selector!r}. Use a user_id instead."
        )

    display_name = raw
    base_id = generate_user_id(display_name)
    user_id = base_id

    existing_ids = {e.user_id for e in entries}
    if user_id in existing_ids:
        suffix = 2
        while True:
            candidate = f"{base_id[:28]}-{suffix}"
            if USER_ID_PATTERN.fullmatch(candidate) and candidate not in existing_ids:
                user_id = candidate
                break
            suffix += 1

    seen_date = date.today().isoformat()
    updated = upsert_person_entry(entries, user_id, display_name, seen_date=seen_date)
    save_people_index(index_path, updated, legacy)
    return user_id, display_name


def ensure_user_memory_file(memory_dir: Path, user_id: str, display_name: str) -> Path:
    """Ensure a user memory folder with basic-info.md and index.md exist.

    Returns path to basic-info.md (the content file).
    """
    user_id = normalize_user_id(user_id)
    user_dir = memory_dir / "people" / user_id
    target = user_dir / "basic-info.md"

    if target.exists():
        return target

    user_dir.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        [
            "# User Memory",
            "",
            "## User ID",
            "",
            user_id,
            "",
            "## Display Name",
            "",
            display_name.strip() or user_id,
            "",
            "## Profile",
            "",
            "-",
            "",
            "## Preferences",
            "",
            "-",
            "",
            "## Relationship",
            "",
            "-",
            "",
            "## Key Memories",
            "",
            "-",
            "",
        ]
    )
    target.write_text(content, encoding="utf-8")

    # Create stub index.md for navigation
    index_path = user_dir / "index.md"
    if not index_path.exists():
        name = display_name.strip() or user_id
        index_content = f"# {name}\n\n- [basic-info](basic-info.md)\n"
        index_path.write_text(index_content, encoding="utf-8")

    return target
