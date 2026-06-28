"""Load eval targets and turn one into a TargetSpec + CFG description for the orchestrator."""
from __future__ import annotations

from pathlib import Path
import re

import yaml

from cloq_agent.lift.cfg import build_cfg, parse_objdump
from cloq_agent.proof.theorem_builder import TargetSpec


def load_targets(path: str | Path) -> dict[str, dict]:
    return yaml.safe_load(Path(path).read_text())


def build_spec(t: dict, repo_root: Path, name: str | None = None):
    spec = TargetSpec(
        name=name or t["lifted_program"],        
        requires=t["requires"],
        lifted_program=t["lifted_program"],
        entry_addr=int(str(t["entry_addr"]), 0),   # accept 0x.. hex
        exit_point=t["exit_point"],
        theorem_name=t["theorem_name"],
        params=[tuple(p) for p in t.get("params", [])],
    )

    cfg_desc = t.get("description", "")
    objdump_rel = t.get("objdump")
    if objdump_rel:
        op = repo_root / "eval" / objdump_rel
        if op.exists():
            cfg = build_cfg(parse_objdump(op.read_text()))
            cfg_desc = f"{cfg_desc}\n{cfg.describe()}"

    secret = t.get("secret_param")

    gold_inv = None
    gi = t.get("gold_invariant")
    if gi:
        gip = (repo_root / "eval" / gi).resolve()
        if gip.exists():
            gold_inv = _extract_invariant(gip.read_text())

    gold_proof = t.get("gold_proof")  # list[str] | None

    return spec, cfg_desc, secret, gold_inv, gold_proof


def _extract_invariant(vsrc: str) -> str | None:
    """Pull a `Definition <name>_timing_invs ... .` (or timing_invs) block from a proof file."""
    m = re.search(
        r"(Definition\s+\w*timing_invs\b.*?end\s*\.)",
        vsrc, re.DOTALL,
    )
    return m.group(1).strip() if m else None
