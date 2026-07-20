"""Annotate provider thinking/reasoning mapping fix for startup upgrade message."""

from pathlib import Path

from .base import Migration


class M0097ProviderThinkingMappingFix(Migration):
    """No-op migration used to surface LLM provider mapping changes to the agent."""

    version = "0.57.2"
    summary = "修正 LLM thinking/reasoning 映射：各 provider 改為各自處理，避免共用抽象導致 thinking 設定失效"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        del kernel_dir, templates_dir
        # No workspace file changes. This migration exists to expose the
        # upgrade summary to the agent via startup heartbeat after upgrade.
        return
