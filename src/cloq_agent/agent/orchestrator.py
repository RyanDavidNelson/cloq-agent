"""The orchestration loop (the diagram in docs/SPEC.md §5, in code).

For a target:
  1. spec-lint the theorem statement (reject trivially-vacuous specs early);
  2. retrieve analogues; ask the model for an invariant set;
  3. render the theorem, hand it to petanque;
  4. try the hammer ladder; on residual goals, retrieve + repair via the LLM;
  5. on Qed, optionally run the FPGA oracle (measured==predicted, secret-invariance);
  6. store the solved proof back into the RAG library.

Budgets bound every loop. Each solved proof makes the next easier (skill accumulation).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..models import LLM
from ..rag.retriever import Retriever
from ..proof.petanque_driver import PetanqueDriver
from ..proof.hammer import try_ladder, run_script
from ..proof.theorem_builder import TargetSpec, render, write
from . import invariant_synth, tactic_repair


@dataclass
class ProofResult:
    target: str
    proved: bool
    invariant_attempt: int
    iterations: int
    llm_calls: int
    escalated: bool
    closing_tactic: str | None
    wall_s: float
    proof_script: list[str] = field(default_factory=list)
    error: str | None = None


def spec_lint(spec: TargetSpec, invariant_src: str, *, secret_param: str | None) -> str | None:
    """Reject specs that are trivially true *by construction*. Returns an error string or None.

    This is anti-vacuity layer #2 from the spec: a constant-time theorem whose invariant never
    mentions the secret is meaningless, so we refuse to even attempt it.
    """
    if secret_param and secret_param not in invariant_src:
        return (
            f"spec rejected: constant-time target '{spec.name}' but secret parameter "
            f"'{secret_param}' does not appear in the invariant set"
        )
    if "cycle_count" not in invariant_src:
        return f"spec rejected: invariant for '{spec.name}' never constrains cycle_count"
    return None


class Orchestrator:
    def __init__(self, cfg: Config, *, fpga_oracle=None):
        self.cfg = cfg
        self.llm = LLM(cfg.model)
        self.retriever = Retriever(cfg.rag)
        self.workspace = Path(cfg.petanque.workspace)
        self.fpga_oracle = fpga_oracle  # callable(target_name, predicted_fn) -> OracleReport|None

    def prove(
        self,
        driver: PetanqueDriver,
        spec: TargetSpec,
        *,
        cfg_description: str,
        secret_param: str | None = None,
        gold_invariant: str | None = None,
        gold_proof: list[str] | None = None,
    ) -> ProofResult:
        t0 = time.time()
        llm_calls = 0
        escalated = False

        for attempt in range(1, self.cfg.agent.invariant_attempts + 1):
            if gold_invariant is not None and attempt == 1:
                invariant_src = gold_invariant
            else:
                retrieved = self.retriever.retrieve(cfg_description)
                escalate = attempt > 1 and self.llm.can_escalate
                invariant_src = invariant_synth.synthesize(
                    self.llm, name=spec.name, entry=spec.entry_addr,
                    cfg_description=cfg_description, retrieved=retrieved, escalate=escalate,
                )
                llm_calls += 1
                escalated = escalated or escalate

            lint = spec_lint(spec, invariant_src, secret_param=secret_param)
            if lint is not None:
                if gold_invariant is not None:
                    return ProofResult(spec.name, False, attempt, 0, llm_calls, escalated,
                                       None, time.time() - t0, error=lint)
                continue

            source = render(spec, invariant_src, _inv_name(invariant_src))
            write(spec, source, self.workspace)
            start = driver.start(
                str(self.workspace / "targets" / f"{spec.name.capitalize()}_gen.v"),
                spec.theorem_name,
            )
            if not start.ok:
                continue

            # Deterministic smoke path: run the gold proof script, no LLM.
            if gold_proof is not None and attempt == 1:
                outcome = run_script(driver, start.state, gold_proof)
                if outcome.closed:
                    return ProofResult(spec.name, True, attempt, len(gold_proof), llm_calls,
                                       escalated, outcome.tactic, time.time() - t0,
                                       proof_script=list(gold_proof))
                return ProofResult(spec.name, False, attempt, len(gold_proof), llm_calls,
                                   escalated, None, time.time() - t0,
                                   proof_script=list(gold_proof),
                                   error=f"gold proof failed at: {outcome.tactic}")

            res = self._discharge(driver, start, spec, attempt, llm_calls, escalated, t0)
            if res.proved:
                if self.fpga_oracle is not None:
                    report = self.fpga_oracle(spec.name)
                    if report is not None and not report.agrees:
                        res.proved = False
                        res.error = f"FPGA disagreement: {report.summary}"
                return res

        return ProofResult(spec.name, False, self.cfg.agent.invariant_attempts, 0,
                           llm_calls, escalated, None, time.time() - t0,
                           error="exhausted invariant attempts")

    def _discharge(self, driver, start, spec, attempt, llm_calls, escalated, t0) -> ProofResult:
        """Close residual goals with hammer-first, LLM-repair fallback under an iteration budget."""
        script: list[str] = []
        state = start.state

        # cheap first pass
        ladder = try_ladder(driver, state)
        if ladder.closed:
            script.append(ladder.tactic or "")
            return ProofResult(spec.name, True, attempt, 0, llm_calls, escalated,
                               ladder.tactic, time.time() - t0, proof_script=script)

        # iterative repair
        cur = driver._result(state, ok=True, error=None)  # refresh goals
        for it in range(1, self.cfg.agent.max_iterations + 1):
            if cur.finished:
                return ProofResult(spec.name, True, attempt, it, llm_calls, escalated,
                                   script[-1] if script else None, time.time() - t0,
                                   proof_script=script)
            if not cur.goals:
                break
            goal = cur.goals[0]
            escalate = it > self.cfg.agent.escalate_after and self.llm.can_escalate
            retrieved = self.retriever.retrieve(goal.conclusion or goal.pretty)
            tactics = tactic_repair.propose(self.llm, goal, retrieved, escalate=escalate)
            llm_calls += 1
            escalated = escalated or escalate

            progressed = False
            for tac in tactics:
                step = driver.run(cur.state, tac)
                if step.ok:
                    script.append(tac)
                    cur = step
                    progressed = True
                    # try to finish immediately after progress
                    quick = try_ladder(driver, cur.state)
                    if quick.closed:
                        script.append(quick.tactic or "")
                        return ProofResult(spec.name, True, attempt, it, llm_calls, escalated,
                                           quick.tactic, time.time() - t0, proof_script=script)
                    break
            if not progressed:
                break

        return ProofResult(spec.name, False, attempt, self.cfg.agent.max_iterations,
                           llm_calls, escalated, None, time.time() - t0,
                           proof_script=script, error="repair budget exhausted")


def _inv_name(invariant_src: str) -> str:
    import re

    m = re.search(r"Definition\s+(\w+)", invariant_src)
    return m.group(1) if m else "timing_invs"
