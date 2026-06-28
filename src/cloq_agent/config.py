"""Configuration loading for cloq-agent.

A thin, dependency-light loader: YAML -> nested dataclasses, with env overrides of the
form CLOQ_<SECTION>_<KEY> (e.g. CLOQ_MODEL_NAME). No magic; everything is explicit so the
config is auditable, which matters for a verification tool.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "default.yaml"


@dataclass
class PetanqueCfg:
    host: str = "127.0.0.1"
    port: int = 8765
    workspace: str = "./proofs"
    timeout_s: int = 120


@dataclass
class EscalationCfg:
    base_url: str | None = None
    api_key: str | None = None
    name: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.name)


@dataclass
class ModelCfg:
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    name: str = "qwen3-coder:30b"
    temperature: float = 0.2
    max_tokens: int = 1024
    escalation: EscalationCfg = field(default_factory=EscalationCfg)


@dataclass
class RagCfg:
    store_dir: str = "./rag_store"
    embedder: str = "local"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_endpoint: str = "http://localhost:11434/v1"
    embed_dim: int = 384
    top_k_lemmas: int = 8
    top_k_proofs: int = 4


@dataclass
class AgentCfg:
    max_iterations: int = 24
    # Local LLM, so attempts are cheap; give synthesis room to self-correct (esp. with the
    # per-attempt error feedback loop in the orchestrator).
    invariant_attempts: int = 12
    hammer_first: bool = True
    escalate_after: int = 16
    # "skeleton": CFG emits the match scaffold + addresses, model fills only the loop/entry
    # holes (postcondition pinned). "freeform": model emits the whole Definition. Kept for A/B.
    synthesis_mode: str = "skeleton"


@dataclass
class FpgaCfg:
    predicted_tolerance_cycles: int = 0
    dudect_trials: int = 2000


@dataclass
class EvalCfg:
    targets_file: str = "./eval/targets.yaml"
    out_dir: str = "./runs"


@dataclass
class Config:
    petanque: PetanqueCfg = field(default_factory=PetanqueCfg)
    model: ModelCfg = field(default_factory=ModelCfg)
    rag: RagCfg = field(default_factory=RagCfg)
    agent: AgentCfg = field(default_factory=AgentCfg)
    fpga: FpgaCfg = field(default_factory=FpgaCfg)
    eval: EvalCfg = field(default_factory=EvalCfg)

def _build(cls: type, data: dict[str, Any]) -> Any:
    """Recursively instantiate a (nested) dataclass from a dict, ignoring unknown keys."""
    hints = get_type_hints(cls)          # resolves stringized annotations -> real classes
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        val = data[f.name]
        ftype = hints.get(f.name, f.type)
        if is_dataclass(ftype) and isinstance(val, dict):
            kwargs[f.name] = _build(ftype, val)
        else:
            kwargs[f.name] = val
    return cls(**kwargs)

def _apply_env(cfg: Config) -> None:
    """Override scalar fields from CLOQ_<SECTION>_<KEY> env vars."""
    for section in fields(cfg):
        sub = getattr(cfg, section.name)
        if not is_dataclass(sub):
            continue
        for f in fields(sub):
            env = f"CLOQ_{section.name.upper()}_{f.name.upper()}"
            if env in os.environ:
                raw = os.environ[env]
                cur = getattr(sub, f.name)
                typed: Any
                if isinstance(cur, bool):
                    typed = raw.lower() in ("1", "true", "yes", "on")
                elif isinstance(cur, int):
                    typed = int(raw)
                elif isinstance(cur, float):
                    typed = float(raw)
                else:
                    typed = raw
                setattr(sub, f.name, typed)


def load_config(path: str | os.PathLike | None = None) -> Config:
    src = Path(path) if path else DEFAULT_CONFIG
    data = yaml.safe_load(src.read_text()) if src.exists() else {}
    cfg = _build(Config, data or {})
    _apply_env(cfg)
    return cfg
