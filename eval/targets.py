"""Load eval targets and turn one into a TargetSpec + CFG description for the orchestrator."""
from __future__ import annotations

from pathlib import Path
import re

import yaml

from cloq_agent.lift.cfg import build_cfg, parse_objdump
from cloq_agent.proof.theorem_builder import TargetSpec


# Top-level keys in targets.yaml that are NOT targets (e.g. named eval groups).
_RESERVED_KEYS = ("groups",)


def load_targets(path: str | Path) -> dict[str, dict]:
    raw = yaml.safe_load(Path(path).read_text())
    return {k: v for k, v in raw.items() if k not in _RESERVED_KEYS}


def load_groups(path: str | Path) -> dict[str, list[str]]:
    """Named eval groups: group name -> list of target names (see `groups:` in targets.yaml)."""
    raw = yaml.safe_load(Path(path).read_text())
    return raw.get("groups", {}) or {}


def resolve_selectors(path: str | Path, selectors: list[str] | None) -> list[str] | None:
    """Expand a mix of group names and target names into a flat target-name list.

    A selector that names a group expands to that group's targets; any other selector is taken
    verbatim as a target name. None/empty means 'all targets' (run_eval treats None as no filter).
    """
    if not selectors:
        return None
    groups = load_groups(path)
    out: list[str] = []
    for s in selectors:
        out.extend(groups[s]) if s in groups else out.append(s)
    return out


# Optional TargetSpec fields the theorem builder reads; absent ones keep their addloop
# defaults (see TargetSpec / the field-doc block at the top of eval/targets.yaml).
_OPTIONAL_SPEC_FIELDS = (
    "timing_functor", "timing_submodule", "program_module",
    "auto_module", "cpu_module", "cpu_config", "postcondition",
    "entry_hyps", "extra_binders",
)


def build_spec(t: dict, repo_root: Path, name: str | None = None):
    overrides = {k: t[k] for k in _OPTIONAL_SPEC_FIELDS if k in t}
    spec = TargetSpec(
        name=name or t["lifted_program"],
        requires=t["requires"],
        lifted_program=t["lifted_program"],
        entry_addr=int(str(t["entry_addr"]), 0),   # accept 0x.. hex
        exit_point=t["exit_point"],
        theorem_name=t["theorem_name"],
        params=[tuple(p) for p in t.get("params", [])],
        **overrides,
    )

    cfg_desc = t.get("description", "")
    skeleton = None
    objdump_rel = t.get("objdump")
    if objdump_rel:
        op = repo_root / "eval" / objdump_rel
        if op.exists():
            cfg = build_cfg(parse_objdump(op.read_text()))
            cfg_desc = f"{cfg_desc}\n{cfg.describe()}"
            # Skeleton synthesis needs both a CFG and a pinned postcondition; otherwise leave it
            # None and the orchestrator falls back to the free-form path.
            if spec.postcondition:
                skeleton = cfg.skeleton_plan(spec)

    secret = t.get("secret_param")

    gold_inv = None
    gi = t.get("gold_invariant")
    if gi:
        gip = (repo_root / "eval" / gi).resolve()
        if gip.exists():
            gold_inv = _extract_invariant(gip.read_text())

    gold_proof = t.get("gold_proof")  # list[str] | None

    return spec, cfg_desc, secret, gold_inv, gold_proof, skeleton


def _extract_invariant(vsrc: str) -> str | None:
    """Pull a `Definition <name>_timing_invs ... .` (or timing_invs) block from a proof file."""
    m = re.search(
        r"(Definition\s+\w*timing_invs\b.*?end\s*\.)",
        vsrc, re.DOTALL,
    )
    return m.group(1).strip() if m else None
