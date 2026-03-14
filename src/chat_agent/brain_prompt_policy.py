"""Runtime policy resolver for the brain system prompt."""

from __future__ import annotations

from pathlib import Path

from .core.schema import AppConfig
from .send_message_batch_guidance import build_prompt_fragment_spec
from .workspace.prompt_resolver import KernelPromptResolver


class BrainPromptPolicy:
    """Resolve brain prompt text from raw kernel prompt plus feature policies."""

    def __init__(self, *, kernel_dir: Path, config: AppConfig):
        self._config = config
        self._resolver = KernelPromptResolver(kernel_dir)

    def resolve(self, raw_prompt: str) -> str:
        """Resolve optional brain prompt fragments from the live kernel."""
        fragments = (
            build_prompt_fragment_spec(
                enabled=self._config.features.send_message_batch_guidance.enabled,
            ),
        )
        return self._resolver.resolve(raw_prompt, fragments=fragments)
