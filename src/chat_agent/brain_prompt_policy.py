"""Runtime policy resolver for the brain system prompt."""

from __future__ import annotations

from pathlib import Path

from .core.schema import AppConfig
from .icloud_sync_awareness import (
    build_prompt_fragment_spec as build_icloud_sync_fragment_spec,
)
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
                enabled=self._feature_enabled("send_message_batch_guidance"),
            ),
            build_icloud_sync_fragment_spec(
                enabled=self._feature_enabled("icloud_sync_awareness"),
            ),
        )
        return self._resolver.resolve(raw_prompt, fragments=fragments)

    def _feature_enabled(self, feature_name: str) -> bool:
        """Allow lightweight test configs to omit unrelated feature sections."""
        features = getattr(self._config, "features", None)
        feature = getattr(features, feature_name, None)
        return bool(getattr(feature, "enabled", False))
