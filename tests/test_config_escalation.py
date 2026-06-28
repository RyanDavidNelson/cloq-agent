"""Escalation must be enableable from the environment so an API key never lands in a committed
config file (this is a public repo)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.config import load_config


def _clear(monkeypatch):
    for k in ("CLOQ_ESCALATION_BASE_URL", "CLOQ_ESCALATION_NAME", "CLOQ_ESCALATION_API_KEY",
              "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_escalation_disabled_by_default(monkeypatch):
    _clear(monkeypatch)
    cfg = load_config()
    assert cfg.model.escalation.enabled is False


def test_escalation_enabled_from_env(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CLOQ_ESCALATION_BASE_URL", "https://api.anthropic.com/v1")
    monkeypatch.setenv("CLOQ_ESCALATION_NAME", "claude-opus-4-8")
    monkeypatch.setenv("CLOQ_ESCALATION_API_KEY", "sk-secret")
    cfg = load_config()
    assert cfg.model.escalation.enabled is True
    assert cfg.model.escalation.name == "claude-opus-4-8"
    assert cfg.model.escalation.api_key == "sk-secret"


def test_escalation_api_key_falls_back_to_standard_env(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CLOQ_ESCALATION_BASE_URL", "https://api.anthropic.com/v1")
    monkeypatch.setenv("CLOQ_ESCALATION_NAME", "claude-opus-4-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-anthropic-env")
    cfg = load_config()
    assert cfg.model.escalation.enabled is True
    assert cfg.model.escalation.api_key == "sk-from-anthropic-env"
