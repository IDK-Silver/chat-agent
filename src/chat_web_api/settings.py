"""Environment-backed settings for the monitoring web API."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm"
    "/main/model_prices_and_context_window.json"
)


@dataclass(frozen=True)
class WebApiSettings:
    host: str = "127.0.0.1"
    port: int = 9002
    sessions_dir: Path = Path()
    static_dir: Path | None = None
    soft_limit_tokens: int = 128_000
    pricing_url: str = _PRICING_URL
    pricing_cache_path: Path = Path()
    pricing_cache_ttl_hours: int = 24

    @classmethod
    def from_env(cls) -> WebApiSettings:
        """Build settings by reading cfgs/agent.yaml."""
        cfgs_dir = Path(__file__).resolve().parent.parent.parent / "cfgs"
        agent_yaml = cfgs_dir / "agent.yaml"
        with open(agent_yaml, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)

        agent_os_dir = Path(cfg["app"]["agent_os_dir"]).expanduser().resolve()
        soft_limit = cfg.get("context", {}).get("soft_max_prompt_tokens", 128_000)

        sessions_dir = agent_os_dir / "session" / "brain"
        pricing_cache_path = agent_os_dir / "state" / "model_pricing_cache.json"

        # Static dir: look for sibling chat_web_ui/dist
        ui_dist = Path(__file__).resolve().parent.parent / "chat_web_ui" / "dist"
        static_dir = ui_dist if ui_dist.is_dir() else None

        return cls(
            sessions_dir=sessions_dir,
            static_dir=static_dir,
            soft_limit_tokens=soft_limit,
            pricing_cache_path=pricing_cache_path,
        )
