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
    # DFS proof-search budgets (orchestrator._discharge). max_iterations is repurposed as the
    # LLM-call cap (propose() calls) inside the search; these bound the shape of the search tree.
    search_max_depth: int = 40      # longest tactic path from the post-start root
    search_max_runs: int = 600      # total driver.run calls across the whole search
    # Ablation switch: when False, _discharge runs the deterministic layer only (ladder +
    # structured prelude + proof library) and skips all LLM tactic-repair. Lets us measure what
    # the LLM search layer actually adds over the deterministic scaffold. Default True (full search).
    llm_repair_enabled: bool = True
    # "skeleton": CFG emits the match scaffold + addresses, model fills only the loop/entry
    # holes (postcondition pinned). "freeform": model emits the whole Definition. Kept for A/B.
    synthesis_mode: str = "skeleton"
    # Per-tactic wall budget (seconds). A hung `repeat step`/`psimpl` is captured as a failed
    # candidate (ok=False, parent state) instead of stalling the whole search. The DFS proof
    # search fires many candidates per node, so each tactic MUST be bounded.
    tactic_timeout_s: float = 20.0


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


def _apply_escalation_env(cfg: Config) -> None:
    """Populate the (nested) escalation model from env vars so an API key never has to be written
    into a committed config file. `_apply_env` only reaches one level deep, so the escalation
    block is handled explicitly here. Set CLOQ_ESCALATION_BASE_URL / _NAME / _API_KEY (the key
    falls back to the standard ANTHROPIC_API_KEY / OPENAI_API_KEY) to turn escalation on."""
    esc = cfg.model.escalation
    if "CLOQ_ESCALATION_BASE_URL" in os.environ:
        esc.base_url = os.environ["CLOQ_ESCALATION_BASE_URL"]
    if "CLOQ_ESCALATION_NAME" in os.environ:
        esc.name = os.environ["CLOQ_ESCALATION_NAME"]
    key = (os.environ.get("CLOQ_ESCALATION_API_KEY")
           or os.environ.get("ANTHROPIC_API_KEY")
           or os.environ.get("OPENAI_API_KEY"))
    if key:
        esc.api_key = key


def load_config(path: str | os.PathLike | None = None) -> Config:
    src = Path(path) if path else DEFAULT_CONFIG
    data = yaml.safe_load(src.read_text()) if src.exists() else {}
    cfg = _build(Config, data or {})
    _apply_env(cfg)
    _apply_escalation_env(cfg)
    return cfg
