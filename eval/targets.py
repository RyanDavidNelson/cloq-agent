"""Load eval targets and turn one into a TargetSpec + CFG description for the orchestrator."""
from __future__ import annotations

from pathlib import Path

import yaml

from cloq_agent.lift.cfg import build_cfg, parse_objdump
from cloq_agent.proof.theorem_builder import TargetSpec


def load_targets(path: str | Path) -> dict[str, dict]:
    return yaml.safe_load(Path(path).read_text())


def build_spec(t: dict, repo_root: Path) -> tuple[TargetSpec, str, str | None, str | None]:
    """Return (spec, cfg_description, secret_param, gold_invariant_source)."""
    spec = TargetSpec(
        name=t["lifted_program"],
        requires=t["requires"],
        lifted_program=t["lifted_program"],
        entry_addr=int(t["entry_addr"]),
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

    return spec, cfg_desc, secret, gold_inv


def _extract_invariant(vsrc: str) -> str | None:
    """Pull the `Definition timing_invs ... .` block out of a checked-in proof file."""
    import re

    m = re.search(r"(Definition\s+timing_invs\b.*?\.\s*$)", vsrc, re.DOTALL | re.MULTILINE)
    return m.group(1).strip() if m else None
