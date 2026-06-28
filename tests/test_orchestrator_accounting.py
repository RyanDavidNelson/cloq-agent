"""prove() must count the search's tactic-repair LLM calls, not just invariant synthesis, in its
final result — otherwise a failed run under-reports its true LLM spend (the bug the eval surfaced).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.agent import orchestrator as orch_mod
from cloq_agent.agent.orchestrator import Orchestrator, ProofResult
from cloq_agent.config import Config
from cloq_agent.proof.petanque_driver import Goal, StepResult
from cloq_agent.proof.theorem_builder import TargetSpec


class _LLM:
    can_escalate = False


class _Retriever:
    def retrieve(self, q):
        return None


class _Driver:
    def start(self, file, theorem):
        return StepResult(ok=True, finished=False, goals=[Goal("g", [], "g")],
                          error=None, state="S0")


_SPEC = TargetSpec(name="t", requires=[], lifted_program="p", entry_addr=0,
                   exit_point="e", theorem_name="thm", params=[])


def test_prove_exhaustion_reports_repair_llm_calls(monkeypatch):
    orch = Orchestrator.__new__(Orchestrator)
    orch.cfg = Config()
    orch.cfg.agent.invariant_attempts = 2
    orch.llm = _LLM()
    orch.retriever = _Retriever()
    orch.workspace = Path("/tmp")
    orch.fpga_oracle = None

    # A type-checking-looking invariant that passes spec_lint (mentions cycle_count, no secret).
    monkeypatch.setattr(orch_mod.invariant_synth, "synthesize",
                        lambda *a, **k: "Definition timing_invs := cycle_count_of_trace t.")
    monkeypatch.setattr(orch_mod, "render", lambda *a, **k: "(* src *)")
    monkeypatch.setattr(orch_mod, "write", lambda *a, **k: Path("/tmp/x.v"))

    # Each _discharge fails but burns 7 repair calls on top of the synthesis call it was handed.
    def _fake_discharge(self, driver, start, spec, attempt, llm_calls, escalated, t0,
                        proof_library=None):
        return ProofResult(spec.name, False, attempt, 3, llm_calls + 7, escalated,
                           None, 0.0, error="residual goal")
    monkeypatch.setattr(Orchestrator, "_discharge", _fake_discharge)

    res = orch.prove(_Driver(), _SPEC, cfg_description="d")
    assert res.proved is False
    # 2 attempts: each adds 1 synthesis + 7 repair = 8; total 16. Pre-fix this would read 2.
    assert res.llm_calls == 16
