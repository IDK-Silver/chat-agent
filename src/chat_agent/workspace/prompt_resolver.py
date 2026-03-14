"""Resolve optional kernel-managed prompt fragments into raw prompt text."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re


@dataclass(frozen=True)
class OptionalKernelPromptFragment:
    """One optional kernel fragment controlled by runtime policy."""

    placeholder: str
    kernel_rel_path: str
    enabled: bool
    legacy_patterns: tuple[re.Pattern[str], ...] = field(default_factory=tuple)


class KernelPromptResolver:
    """Resolve kernel prompt placeholders against live kernel fragment files."""

    def __init__(self, kernel_dir: Path):
        self._kernel_dir = kernel_dir

    def resolve(
        self,
        raw_prompt: str,
        *,
        fragments: tuple[OptionalKernelPromptFragment, ...] = (),
    ) -> str:
        """Resolve optional prompt fragments into the raw prompt text."""
        resolved = raw_prompt

        for fragment in fragments:
            if fragment.placeholder in resolved:
                replacement = ""
                if fragment.enabled:
                    replacement = self._read_fragment(fragment.kernel_rel_path)
                resolved = resolved.replace(fragment.placeholder, replacement)
                continue

            if not fragment.enabled:
                for pattern in fragment.legacy_patterns:
                    resolved = pattern.sub("\n", resolved)

        while "\n\n\n" in resolved:
            resolved = resolved.replace("\n\n\n", "\n\n")
        return resolved.strip() + "\n"

    def _read_fragment(self, kernel_rel_path: str) -> str:
        """Load and normalize one kernel fragment file."""
        fragment_path = self._kernel_dir / kernel_rel_path
        return fragment_path.read_text(encoding="utf-8").strip()
