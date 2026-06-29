"""Config profiles: local (Ollama + cloud escalation) and api (cloud, escalation off).

Guards the Phase 6 contract, including "do not change default.yaml's current behavior".
"""
from __future__ import annotations

import pytest

from cloq_agent.config import load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Profiles/keys must come only from the test, not the ambient shell.
    for k in ("CLOQ_PROFILE", "CLOQ_API_KEY", "CLOQ_MODEL_NAME", "CLOQ_MODEL_BASE_URL",
              "CLOQ_MODEL_API_KEY", "CLOQ_ESCALATION_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_default_behavior_unchanged():
    d = load_config()
    assert d.model.base_url == "http://localhost:11434/v1"
    assert d.model.name == "qwen3-coder:30b"
    assert d.model.api_key == "ollama"
    assert d.model.escalation.enabled is False


def test_local_profile_ollama_primary_with_cloud_escalation():
    c = load_config(profile="local")
    assert "11434" in c.model.base_url                  # Ollama primary
    assert c.model.escalation.enabled is True           # cloud escalation on
    assert c.model.escalation.name == "claude-opus-4-8"
    # inherits non-model sections from default.yaml (overlay merge)
    assert c.petanque.port == 8765
    assert c.agent.invariant_attempts == 12


def test_api_profile_cloud_primary_escalation_off():
    c = load_config(profile="api")
    assert c.model.base_url.startswith("https://")       # cloud, not Ollama
    assert c.model.escalation.enabled is False


def test_cloq_api_key_maps_to_primary_and_escalation(monkeypatch):
    monkeypatch.setenv("CLOQ_API_KEY", "sk-secret")
    assert load_config(profile="api").model.api_key == "sk-secret"
    # local: Ollama ignores the primary key; the cloud escalation picks it up.
    local = load_config(profile="local")
    assert local.model.escalation.api_key == "sk-secret"


def test_cloq_profile_env_selects_profile(monkeypatch):
    monkeypatch.setenv("CLOQ_PROFILE", "api")
    assert load_config().model.base_url.startswith("https://")


def test_unknown_profile_raises():
    with pytest.raises(FileNotFoundError):
        load_config(profile="nope")


def test_explicit_config_path_overrides_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOQ_PROFILE", "api")
    cfg_file = tmp_path / "custom.yaml"
    cfg_file.write_text("model:\n  name: my-model\n  base_url: http://x:1/v1\n")
    c = load_config(path=str(cfg_file))
    assert c.model.name == "my-model"          # path wins over CLOQ_PROFILE
    assert c.model.base_url == "http://x:1/v1"
