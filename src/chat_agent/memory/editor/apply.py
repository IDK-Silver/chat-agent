"""Deterministic apply functions for memory writer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ...tools.security import is_path_allowed
from .schema import MemoryEditRequest


_CHECKBOX_PATTERN = re.compile(r"^(?P<prefix>\s*-\s*\[)(?P<state>[ xX])(?P<suffix>\]\s+.*)$")
_MEMORY_ROOT_HINTS = {"agent", "people", "short-term.md"}


@dataclass
class ApplyOutcome:
    """Outcome for one deterministic apply attempt."""

    status: str  # applied | noop | error
    code: str | None = None
    detail: str | None = None


def resolve_memory_path(
    path: str,
    *,
    allowed_paths: list[str],
    base_dir: Path,
) -> Path:
    """Resolve and validate a memory path."""
    normalized = path.replace("\\", "/")
    if not normalized.startswith("memory/"):
        raise ValueError("target_path must start with 'memory/'")

    if not is_path_allowed(normalized, allowed_paths, base_dir):
        raise ValueError(f"Path '{normalized}' is not allowed")

    target = base_dir / normalized
    resolved = target.resolve()
    try:
        resolved.relative_to(base_dir.resolve())
    except ValueError as e:
        raise ValueError("Path escapes working directory") from e
    return resolved


def apply_request(
    request: MemoryEditRequest,
    *,
    allowed_paths: list[str],
    base_dir: Path,
) -> ApplyOutcome:
    """Apply one request with deterministic logic."""
    try:
        target = resolve_memory_path(
            request.target_path,
            allowed_paths=allowed_paths,
            base_dir=base_dir,
        )
    except ValueError as e:
        return ApplyOutcome(status="error", code="path_invalid", detail=str(e))

    try:
        if request.kind == "create_if_missing":
            return _create_if_missing(target, request.payload_text or "")
        if request.kind == "append_entry":
            return _append_entry(target, request.payload_text or "")
        if request.kind == "replace_block":
            return _replace_block(
                target,
                old_block=request.old_block or "",
                new_block=request.new_block or "",
                replace_all=bool(request.replace_all),
            )
        if request.kind == "toggle_checkbox":
            return _toggle_checkbox(
                target,
                item_text=request.item_text or "",
                checked=bool(request.checked),
            )
        if request.kind == "ensure_index_link":
            index_path = request.index_path or request.target_path
            try:
                index_target = resolve_memory_path(
                    index_path,
                    allowed_paths=allowed_paths,
                    base_dir=base_dir,
                )
            except ValueError as e:
                return ApplyOutcome(status="error", code="path_invalid", detail=str(e))
            return _ensure_index_link(
                index_target,
                link_path=request.link_path or "",
                link_title=request.link_title or "",
                base_dir=base_dir,
            )
    except Exception as e:
        return ApplyOutcome(status="error", code="apply_exception", detail=str(e))

    return ApplyOutcome(status="error", code="unsupported_kind", detail=request.kind)


def _create_if_missing(target: Path, payload: str) -> ApplyOutcome:
    if target.exists():
        if not target.is_file():
            return ApplyOutcome(status="error", code="not_a_file", detail=str(target))
        return ApplyOutcome(status="noop")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    if target.read_text(encoding="utf-8") != payload:
        return ApplyOutcome(status="error", code="verify_failed", detail="create_if_missing")
    return ApplyOutcome(status="applied")


def _append_entry(target: Path, payload: str) -> ApplyOutcome:
    if not target.exists():
        return ApplyOutcome(status="error", code="file_not_found", detail=str(target))
    if not target.is_file():
        return ApplyOutcome(status="error", code="not_a_file", detail=str(target))

    content = target.read_text(encoding="utf-8")
    if payload in content:
        return ApplyOutcome(status="noop")

    sep = "" if content.endswith("\n") or not content else "\n"
    updated = content + sep + payload
    target.write_text(updated, encoding="utf-8")
    verify = target.read_text(encoding="utf-8")
    if payload not in verify:
        return ApplyOutcome(status="error", code="verify_failed", detail="append_entry")
    return ApplyOutcome(status="applied")


def _replace_block(
    target: Path,
    *,
    old_block: str,
    new_block: str,
    replace_all: bool,
) -> ApplyOutcome:
    if not target.exists():
        return ApplyOutcome(status="error", code="file_not_found", detail=str(target))
    if not target.is_file():
        return ApplyOutcome(status="error", code="not_a_file", detail=str(target))

    content = target.read_text(encoding="utf-8")
    matches = content.count(old_block)

    if matches == 0:
        # Idempotent replay where replacement has already been applied.
        if new_block in content:
            return ApplyOutcome(status="noop")
        return ApplyOutcome(status="error", code="block_not_found", detail=old_block)

    if matches > 1 and not replace_all:
        return ApplyOutcome(
            status="error",
            code="multiple_matches",
            detail=f"{matches} matches for old_block",
        )

    updated = (
        content.replace(old_block, new_block)
        if replace_all
        else content.replace(old_block, new_block, 1)
    )
    if updated == content:
        return ApplyOutcome(status="noop")

    target.write_text(updated, encoding="utf-8")
    verify = target.read_text(encoding="utf-8")
    if verify != updated:
        return ApplyOutcome(status="error", code="verify_failed", detail="replace_block")
    return ApplyOutcome(status="applied")


def _toggle_checkbox(target: Path, *, item_text: str, checked: bool) -> ApplyOutcome:
    if not target.exists():
        return ApplyOutcome(status="error", code="file_not_found", detail=str(target))
    if not target.is_file():
        return ApplyOutcome(status="error", code="not_a_file", detail=str(target))

    lines = target.read_text(encoding="utf-8").splitlines()
    matches: list[int] = []
    for i, line in enumerate(lines):
        if item_text in line and _CHECKBOX_PATTERN.match(line):
            matches.append(i)

    if not matches:
        return ApplyOutcome(status="error", code="item_not_found", detail=item_text)
    if len(matches) > 1:
        return ApplyOutcome(
            status="error",
            code="multiple_matches",
            detail=f"{len(matches)} matches for '{item_text}'",
        )

    idx = matches[0]
    match = _CHECKBOX_PATTERN.match(lines[idx])
    if match is None:
        return ApplyOutcome(status="error", code="invalid_checkbox_line", detail=lines[idx])

    desired = "x" if checked else " "
    current = match.group("state").lower()
    if current == desired:
        return ApplyOutcome(status="noop")

    lines[idx] = f"{match.group('prefix')}{desired}{match.group('suffix')}"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ApplyOutcome(status="applied")


def _memory_root(base_dir: Path) -> Path:
    """Resolve the workspace memory root."""
    return (base_dir / "memory").resolve()


def _normalize_link_path(link_path: str, *, index_target: Path, base_dir: Path) -> str | None:
    """Normalize link paths to canonical memory/... form."""
    raw = link_path.strip().replace("\\", "/")
    if not raw:
        return None

    if raw.startswith("memory/"):
        candidate = raw
    elif raw.startswith("./memory/"):
        candidate = raw[2:]
    elif raw.startswith(".agent/memory/"):
        candidate = "memory/" + raw[len(".agent/memory/") :]
    elif "/memory/" in raw:
        candidate = "memory/" + raw.split("/memory/", 1)[1].lstrip("/")
    else:
        memory_root = _memory_root(base_dir)
        raw_path = Path(raw)
        if raw_path.is_absolute():
            return None

        normalized_relative = raw[2:] if raw.startswith("./") else raw
        top_level = normalized_relative.split("/", 1)[0]

        if top_level in _MEMORY_ROOT_HINTS:
            resolved = (memory_root / normalized_relative).resolve()
        else:
            resolved = (index_target.parent / raw_path).resolve()
            try:
                resolved.relative_to(memory_root)
            except ValueError:
                resolved = (memory_root / normalized_relative).resolve()

        try:
            rel = resolved.relative_to(memory_root)
        except ValueError:
            return None
        candidate = f"memory/{rel.as_posix()}"

    memory_root = _memory_root(base_dir)
    try:
        resolved = (base_dir / candidate).resolve()
        resolved.relative_to(memory_root)
    except ValueError:
        return None
    return candidate


def _ensure_index_link(
    index_target: Path,
    *,
    link_path: str,
    link_title: str,
    base_dir: Path,
) -> ApplyOutcome:
    normalized_link_path = _normalize_link_path(
        link_path,
        index_target=index_target,
        base_dir=base_dir,
    )
    if normalized_link_path is None:
        return ApplyOutcome(
            status="error",
            code="link_path_invalid",
            detail="link_path must resolve under memory/",
        )

    if index_target.exists() and not index_target.is_file():
        return ApplyOutcome(status="error", code="not_a_file", detail=str(index_target))

    if not index_target.exists():
        index_target.parent.mkdir(parents=True, exist_ok=True)
        index_target.write_text("# Index\n\n", encoding="utf-8")

    content = index_target.read_text(encoding="utf-8")
    if f"({normalized_link_path})" in content or f"({link_path})" in content:
        return ApplyOutcome(status="noop")

    link_line = f"- [{link_title}]({normalized_link_path})"
    sep = "" if content.endswith("\n") or not content else "\n"
    updated = content + sep + link_line + "\n"
    index_target.write_text(updated, encoding="utf-8")
    if f"({normalized_link_path})" not in index_target.read_text(encoding="utf-8"):
        return ApplyOutcome(status="error", code="verify_failed", detail="ensure_index_link")
    return ApplyOutcome(status="applied")
