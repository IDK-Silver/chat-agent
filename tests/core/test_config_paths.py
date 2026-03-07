from pathlib import Path

import yaml

from chat_agent.core import config as config_module


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def test_resolve_llm_config_accepts_cfgs_prefix(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "openai" / "profile.yaml",
        {
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "test-key",
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    config = config_module.resolve_llm_config("cfgs/llm/openai/profile.yaml")
    assert config.model == "gpt-4o"


def test_load_config_accepts_cfgs_prefixed_llm_path(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "openai" / "profile.yaml",
        {
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "test-key",
        },
    )
    _write_yaml(
        tmp_path / "basic.yaml",
        {"agents": {"brain": {"llm": "cfgs/llm/openai/profile.yaml"}}},
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    config = config_module.load_config("basic.yaml")
    assert config.agents["brain"].llm.model == "gpt-4o"
