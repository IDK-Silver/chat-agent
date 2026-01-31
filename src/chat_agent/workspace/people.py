"""People memory utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import re
from pathlib import Path


USER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


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
        if line.strip().lower() == "| user_id | display_name | aliases | last_seen |":
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

    if entries:
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
        "Files are named: `user-{user_id}.md`",
        "",
        "## People",
        "",
        "| user_id | display_name | aliases | last_seen |",
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
    """Ensure a user memory file exists and return its path."""
    people_dir = memory_dir / "people"
    people_dir.mkdir(parents=True, exist_ok=True)

    user_id = normalize_user_id(user_id)
    target = people_dir / f"user-{user_id}.md"

    if target.exists():
        return target

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
    return target

