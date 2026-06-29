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
    temperature: float | None = 0.2     # None -> omit (some models, e.g. Opus 4.8, forbid it)
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
    # Clamped budgets applied ONLY to a ceiling-classified target run under --force-synthesis: the
    # CFG has no deterministic invariant, so synthesis is a long shot — fail fast with the
    # diagnostic instead of burning the full invariant_attempts x search_max_runs budget churning.
    ceiling_invariant_attempts: int = 1
    ceiling_search_max_runs: int = 40
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
           or os.environ.get("CLOQ_API_KEY")
           or os.environ.get("ANTHROPIC_API_KEY")
           or os.environ.get("OPENAI_API_KEY"))
    if key:
        esc.api_key = key


def _apply_key_env(cfg: Config) -> None:
    """`CLOQ_API_KEY` is the one canonical key var (see .env.example). It sets the PRIMARY model's
    key — which is what the `api` (cloud-primary) profile needs; it is harmless for the `local`
    profile (Ollama ignores the key, and the escalation block picks the key up separately)."""
    key = os.environ.get("CLOQ_API_KEY")
    if key:
        cfg.model.api_key = key


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def resolve_out_dir(out_dir: str, repo_root: str | os.PathLike) -> Path:
    """Resolve an `eval.out_dir` config value to a real path. A relative value (the default,
    `./runs`) is taken relative to the repo root; an absolute value is honoured as-is. Avoids the
    `str.lstrip("./")` pitfall that silently turns an absolute `/abs/path` into a relative one."""
    p = Path(out_dir)
    return p if p.is_absolute() else Path(repo_root) / out_dir.lstrip("./")


def load_config(path: str | os.PathLike | None = None, profile: str | None = None) -> Config:
    """Load configuration, then apply `CLOQ_*` env overrides.

    Resolution order:
      * explicit `path`  -> that file alone (back-compatible `--config` behaviour);
      * else a `profile` (arg or `CLOQ_PROFILE` env), e.g. `local` / `api` -> `config/<profile>.yaml`
        OVERLAID on `config/default.yaml` (so a profile only states what differs);
      * else `config/default.yaml` (unchanged default behaviour).
    """
    profile = profile or os.environ.get("CLOQ_PROFILE")
    base = yaml.safe_load(DEFAULT_CONFIG.read_text()) if DEFAULT_CONFIG.exists() else {}

    if path:
        data = yaml.safe_load(Path(path).read_text()) or {}
    elif profile:
        prof_path = DEFAULT_CONFIG.parent / f"{profile}.yaml"
        if not prof_path.exists():
            raise FileNotFoundError(
                f"unknown config profile '{profile}' (looked for {prof_path}). "
                f"known profiles: local, api"
            )
        data = _deep_merge(base or {}, yaml.safe_load(prof_path.read_text()) or {})
    else:
        data = base or {}

    cfg = _build(Config, data or {})
    _apply_env(cfg)
    _apply_escalation_env(cfg)
    _apply_key_env(cfg)
    return cfg
