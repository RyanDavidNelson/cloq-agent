"""End-to-end search against a REAL pet-server (skipped when none is reachable).

These run inside the agent container, which mounts the workspace and can reach the rocq
container's pet-server. addloop is a destruct-requiring target: its 0x10 loop point branches
taken/not-taken, so the proof must `destruct_inv 32 PRE` into two arms — exactly the case-split
the DFS search exists for.
"""
from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.config import load_config
from cloq_agent.agent.orchestrator import Orchestrator, _inv_name
from cloq_agent.proof.petanque_driver import PetanqueDriver
from cloq_agent.proof.theorem_builder import render, write

_REPO = Path(__file__).resolve().parents[1]
_CFG = load_config()


def _pet_server_up() -> bool:
    try:
        with socket.create_connection((_CFG.petanque.host, _CFG.petanque.port), timeout=1.0):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _pet_server_up(),
    reason=f"no pet-server at {_CFG.petanque.host}:{_CFG.petanque.port}",
)


def _addloop():
    """(spec, gold_invariant, gold_proof) for addloop from the eval registry."""
    from eval.targets import load_targets, build_spec

    t = load_targets(str(_REPO / "eval" / "targets.yaml"))["addloop"]
    spec, _desc, _secret, gold_inv, gold_proof, _sk = build_spec(t, _REPO, name="addloop")
    return spec, gold_inv, gold_proof


def _bare_orchestrator() -> Orchestrator:
    """Orchestrator without the heavy embedder/LLM init — the gold path never touches them."""
    orch = Orchestrator.__new__(Orchestrator)
    orch.cfg = _CFG
    orch.llm = None
    orch.retriever = None
    orch.workspace = Path(_CFG.petanque.workspace)
    orch.fpga_oracle = None
    return orch


def _server_path(spec) -> str:
    return f"{_CFG.petanque.workspace}/targets/{spec.name.capitalize()}_gen.v"


def test_search_closes_destruct_target_via_library_script():
    """The DFS search closes a destruct-requiring proof (addloop) deterministically: the root
    expansion plays the reusable proof-library script, which fans out with `destruct_inv` and
    closes both arms -> state.finished. No LLM calls. Asserts the real proof reached Qed."""
    spec, gold_inv, gold_proof = _addloop()
    orch = _bare_orchestrator()
    # Render the OPEN proof and load it, exactly as prove() does, then drive _discharge directly.
    write(spec, render(spec, gold_inv, _inv_name(gold_inv)), orch.workspace)
    with PetanqueDriver(_CFG.petanque, default_timeout_s=_CFG.agent.tactic_timeout_s) as d:
        start = d.start(_server_path(spec), spec.theorem_name)
        assert start.ok, f"start failed: {start.error}"
        res = orch._discharge(d, start, spec, attempt=1, llm_calls=0, escalated=False,
                              t0=time.time(), proof_library=[gold_proof])
    assert res.proved is True
    assert res.llm_calls == 0                          # deterministic close, no model
    assert "destruct_inv 32 PRE." in res.proof_script  # the proof genuinely case-split


def test_gold_path_prove_addloop_closes_with_no_llm_calls():
    """Regression: `prove addloop` via the gold deterministic path still closes with llm_calls == 0
    (the smoke invariant we must never regress). prove() returns before _discharge here."""
    spec, gold_inv, gold_proof = _addloop()
    orch = _bare_orchestrator()
    with PetanqueDriver(_CFG.petanque, default_timeout_s=_CFG.agent.tactic_timeout_s) as d:
        res = orch.prove(d, spec, cfg_description="addloop smoke",
                         gold_invariant=gold_inv, gold_proof=gold_proof)
    assert res.proved is True
    assert res.llm_calls == 0
    assert res.proof_script == gold_proof
