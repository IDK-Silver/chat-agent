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


def test_resolve_llm_config_reads_ollama_api_key_from_env(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "ollama" / "cloud.yaml",
        {
            "provider": "ollama",
            "model": "gpt-oss:20b-cloud",
            "base_url": "https://ollama.com",
            "api_key_env": "OLLAMA_API_KEY",
            "thinking": {"mode": "effort", "effort": "medium"},
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)
    monkeypatch.setenv("OLLAMA_API_KEY", "env-ollama-key")

    config = config_module.resolve_llm_config("llm/ollama/cloud.yaml")
    assert config.api_key == "env-ollama-key"

