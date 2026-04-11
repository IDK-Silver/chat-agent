"""Shared helpers for optional iCloud-sync awareness in the brain prompt."""

from __future__ import annotations

from .workspace.prompt_resolver import OptionalKernelPromptFragment

SYSTEM_PROMPT_PLACEHOLDER = "{icloud_sync_awareness}"
SYSTEM_PROMPT_FRAGMENT_PATH = (
    "agents/brain/prompts/fragments/icloud-sync-awareness.md"
)


def build_prompt_fragment_spec(*, enabled: bool) -> OptionalKernelPromptFragment:
    """Build the system-prompt fragment spec for iCloud-sync awareness."""
    return OptionalKernelPromptFragment(
        placeholder=SYSTEM_PROMPT_PLACEHOLDER,
        kernel_rel_path=SYSTEM_PROMPT_FRAGMENT_PATH,
        enabled=enabled,
    )
