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

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..models import LLM
from ..rag.retriever import Retriever
from ..proof.petanque_driver import PetanqueDriver
from ..proof.hammer import try_ladder, run_script, STRUCTURED_SCRIPTS
from ..proof.theorem_builder import TargetSpec, render, write
from . import invariant_synth, tactic_repair

log = logging.getLogger("cloq_agent.orchestrator")


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
    # The last residual proof obligation when the search failed (for the diagnostic report).
    residual_goal: str | None = None
    # True when the solved invariant+proof was persisted into the RAG corpus (success only).
    stored_to_corpus: bool = False


@dataclass
class _SearchNode:
    """A node in the DFS proof search = a petanque state plus the tactic path that produced it.

    The state carries the FULL goal stack, so a multi-subgoal `destruct_inv` fan-out is one node
    (solved iff `state.finished`) — the search never hand-manages sibling subgoals. `path` is kept
    so a stale handle can be reconstructed via `driver.replay_from_root` (Task 1 safety net)."""
    state: object
    path: list[str]
    depth: int


class _RunCounter:
    """Thin proxy that counts every `driver.run` against the search's total-runs budget, while
    transparently forwarding the other driver methods the hammer ladder / scripts use. Lets the
    budget cover runs made *inside* try_ladder / run_script, not just the explicit ones."""

    def __init__(self, driver, budget: int):
        self._driver = driver
        self._budget = budget
        self.runs = 0

    @property
    def over(self) -> bool:
        return self.runs >= self._budget

    def run(self, state, tactic, timeout_s=None):
        self.runs += 1
        return self._driver.run(state, tactic, timeout_s)

    def _result(self, state, *, ok, error):
        return self._driver._result(state, ok=ok, error=error)

    def replay_from_root(self, file, theorem, path):
        return self._driver.replay_from_root(file, theorem, path)


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
    # NOTE: a per-arm `cycle_count_of_trace` check was tried and removed — it false-rejects the
    # legitimate EXIT arm, which references a named postcondition predicate (`time_of_X t ...`)
    # whose body holds the cycle equation elsewhere, so the literal token isn't in the arm.
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
        invariant_skeleton=None,
        proof_library: list[list[str]] | None = None,
    ) -> ProofResult:
        t0 = time.time()
        llm_calls = 0
        escalated = False
        # Carries the previous attempt's failure (Rocq/lint error) into the next synthesis call so
        # the model can correct a concrete mistake instead of re-guessing blind.
        last_error: str | None = None

        for attempt in range(1, self.cfg.agent.invariant_attempts + 1):
            if gold_invariant is not None and attempt == 1:
                invariant_src = gold_invariant
            else:
                retrieved = self.retriever.retrieve(cfg_description)
                escalate = attempt > 1 and self.llm.can_escalate
                # Skeleton mode only applies when the CFG produced a skeleton (objdump +
                # pinned postcondition); otherwise this falls back to the free-form path.
                mode = self.cfg.agent.synthesis_mode if invariant_skeleton is not None else "freeform"
                invariant_src = invariant_synth.synthesize(
                    self.llm, name=spec.name, entry=spec.entry_addr,
                    cfg_description=cfg_description, retrieved=retrieved, escalate=escalate,
                    mode=mode, skeleton=invariant_skeleton,
                    params=spec.params, feedback=last_error,
                )
                llm_calls += 1
                escalated = escalated or escalate
                log.info(
                    "[%s] attempt %d: %s-mode model-proposed invariant (escalate=%s):\n%s",
                    spec.name, attempt, mode, escalate, invariant_src,
                )

            lint = spec_lint(spec, invariant_src, secret_param=secret_param)
            if lint is not None:
                if gold_invariant is not None:
                    return ProofResult(spec.name, False, attempt, 0, llm_calls, escalated,
                                       None, time.time() - t0, error=lint)
                last_error = lint
                continue

            source = render(spec, invariant_src, _inv_name(invariant_src))
            write(spec, source, self.workspace)
            start = driver.start(
                str(self.workspace / "targets" / f"{spec.name.capitalize()}_gen.v"),
                spec.theorem_name,
            )
            if not start.ok:
                last_error = start.error
                continue

            # Deterministic smoke path: run the gold proof script, no LLM.
            if gold_proof is not None and attempt == 1:
                outcome = run_script(driver, start.state, gold_proof)
                if outcome.closed:
                    stored = self._store_solved(spec, invariant_src, list(gold_proof))
                    return ProofResult(spec.name, True, attempt, len(gold_proof), llm_calls,
                                       escalated, outcome.tactic, time.time() - t0,
                                       proof_script=list(gold_proof), stored_to_corpus=stored)
                return ProofResult(spec.name, False, attempt, len(gold_proof), llm_calls,
                                   escalated, None, time.time() - t0,
                                   proof_script=list(gold_proof),
                                   error=f"gold proof failed at: {outcome.tactic}")

            res = self._discharge(driver, start, spec, attempt, llm_calls, escalated, t0,
                                  proof_library=proof_library)
            # Carry the search's tactic-repair LLM calls back into our running total — `_discharge`
            # returns `llm_calls + repair_calls`, so the final report counts repair, not just
            # synthesis (otherwise a failed run under-reports its true LLM spend).
            llm_calls = res.llm_calls
            escalated = res.escalated
            if res.proved:
                if self.fpga_oracle is not None:
                    report = self.fpga_oracle(spec.name)
                    if report is not None and not report.agrees:
                        res.proved = False
                        res.error = f"FPGA disagreement: {report.summary}"
                if res.proved:
                    res.stored_to_corpus = self._store_solved(spec, invariant_src, res.proof_script)
                return res
            # Discharge failed (invariant type-checked but the proof didn't close); feed that back.
            last_error = res.error

        return ProofResult(spec.name, False, self.cfg.agent.invariant_attempts, 0,
                           llm_calls, escalated, None, time.time() - t0,
                           error="exhausted invariant attempts")

    def _store_solved(self, spec: TargetSpec, invariant_src: str,
                      proof_script: list[str] | None) -> bool:
        """Persist a solved invariant + proof into the RAG corpus so the *next* retrieve can reuse
        it (skill accumulation). Adds one `proof` record to the live store and saves it to the
        mounted `rag_store/` volume. Best-effort: a corpus write must never fail a sound proof.
        """
        from ..rag.store import Record

        try:
            store = self.retriever.store
            body = "\n".join(proof_script or [])
            text = (
                f"(* solved by cloq-agent: {spec.name} ({spec.theorem_name}) *)\n"
                f"{invariant_src.strip()}\n\nProof.\n{body}\nQed."
            )
            rec = Record(
                id=f"solved::{spec.name}",
                text=text,
                kind="proof",
                meta={"target": spec.name, "theorem": spec.theorem_name, "source": "orchestrator"},
            )
            store.add(rec, self.retriever.embedder.embed_one(text))
            store.save(self.cfg.rag.store_dir)
            log.info("[%s] added solved proof to RAG corpus -> %s", spec.name, self.cfg.rag.store_dir)
            return True
        except Exception as e:  # never let corpus surfacing break a proof
            log.warning("[%s] store-back to RAG corpus failed: %s", spec.name, e)
            return False

    def _discharge(self, driver, start, spec, attempt, llm_calls, escalated, t0,
                   proof_library=None) -> ProofResult:
        """Depth-first proof search WITH BACKTRACKING over petanque states.

        A node is a petanque state; success is `state.finished` (petanque holds the whole
        multi-subgoal conjunction, so we never hand-manage sibling subgoals). At each node we try
        the cheap ladder first (hammer-first), then expand: the deterministic structural prelude at
        the root, LLM tactic-repair candidates deeper. Candidates that make progress (a new goal
        hash) become children pushed best-first onto a LIFO stack; a dead-end branch is abandoned by
        popping back to the parent's next candidate. Budgets bound depth, total runs, and LLM calls.
        """
        cfg = self.cfg.agent
        file = str(self.workspace / "targets" / f"{spec.name.capitalize()}_gen.v")
        theorem = spec.theorem_name
        cd = _RunCounter(driver, cfg.search_max_runs)

        # Cheap-first on the raw start state (straight-line / near-gold closes with no search).
        quick = try_ladder(cd, start.state)
        if quick.closed:
            return ProofResult(spec.name, True, attempt, 0, llm_calls, escalated,
                               quick.tactic, time.time() - t0,
                               proof_script=[quick.tactic or ""])

        frontier: list[_SearchNode] = [_SearchNode(start.state, [], 0)]
        visited: set[str] = {_goal_hash(start.goals)}
        # past_failures keyed by goal-hash: a tactic that failed on this exact goal is never re-proposed.
        failures: dict[str, set[str]] = {}
        repair_calls = 0          # LLM propose() calls — capped by max_iterations
        nodes = 0
        deepest: list[str] = []
        residual = start.goals

        def success(path: list[str], closing: str | None) -> ProofResult:
            return ProofResult(spec.name, True, attempt, nodes, llm_calls + repair_calls,
                               escalated, closing, time.time() - t0, proof_script=path)

        while frontier and not cd.over:
            node = frontier.pop()
            nodes += 1

            # Refresh goals from the stored handle; replay from root if the handle is stale (Task 1
            # found handles stay live, so this is just a safety net).
            try:
                cur = cd._result(node.state, ok=True, error=None)
            except Exception:
                cur = cd.replay_from_root(file, theorem, node.path)
                if not cur.ok:
                    continue

            if cur.finished:
                return success(node.path, node.path[-1] if node.path else None)
            if not cur.goals:
                continue
            if len(node.path) > len(deepest):
                deepest, residual = node.path, cur.goals

            # Hammer-first at every node.
            lad = try_ladder(cd, cur.state)
            if lad.closed:
                return success(node.path + [lad.tactic or ""], lad.tactic)

            if node.depth >= cfg.search_max_depth or cd.over:
                continue

            children: list[_SearchNode] = []

            # Root only: the deterministic structural prelude (apply prove_invs … destruct_inv) and
            # the reusable proof-skill library. These play multi-tactic scripts that advance the raw
            # `satisfies_all` goal to the post-`destruct_inv` fan-out — not LLM territory.
            if node.depth == 0:
                for script in [*STRUCTURED_SCRIPTS, *(proof_library or [])]:
                    if cd.over:
                        break
                    out = run_script(cd, cur.state, list(script))
                    if out.closed:
                        return success(node.path + list(script), script[-1] if script else None)
                    h = _goal_hash(out.residual)
                    if out.residual and h not in visited:
                        visited.add(h)
                        children.append(_SearchNode(out.state, node.path + list(script), node.depth + 1))

            # LLM repair on goals[0] — when the deterministic prelude produced nothing (root) or at
            # any deeper fan-out node, under the LLM-call cap. Skipped entirely in the
            # deterministic-only ablation (`llm_repair_enabled=False`).
            if (not children and cfg.llm_repair_enabled
                    and repair_calls < cfg.max_iterations and not cd.over):
                goal = cur.goals[0]
                past = failures.setdefault(_goal_hash(cur.goals), set())
                escalate = repair_calls >= cfg.escalate_after and self.llm.can_escalate
                retrieved = self.retriever.retrieve(goal.conclusion or goal.pretty)
                tactics = tactic_repair.propose(self.llm, goal, retrieved,
                                                escalate=escalate, past_failures=past)
                repair_calls += 1
                escalated = escalated or escalate
                for tac in tactics:
                    if cd.over:
                        break
                    step = cd.run(cur.state, tac)
                    if not step.ok:
                        past.add(tac)              # remember this dead end for this goal
                        continue
                    if step.finished:
                        return success(node.path + [tac], tac)
                    h = _goal_hash(step.goals)
                    if h in visited:               # prune revisits / cycles
                        continue
                    visited.add(h)
                    children.append(_SearchNode(step.state, node.path + [tac], node.depth + 1))

            # Push so the most-promising candidate (propose() is best-first) is popped FIRST (LIFO).
            for child in reversed(children):
                frontier.append(child)

        # No path closed: surface the deepest residual obligation so the orchestrator can feed it
        # into the next synthesis attempt (verifier-guided refinement), else a budget note.
        residual_goal = None
        if residual:
            g = residual[0]
            residual_goal = (g.conclusion or g.pretty or "").strip() or None
        return ProofResult(spec.name, False, attempt, nodes, llm_calls + repair_calls,
                           escalated, None, time.time() - t0, proof_script=deepest,
                           residual_goal=residual_goal,
                           error=_unproved_goal_msg(residual) if residual else "search budget exhausted")


def _goal_hash(goals) -> str:
    """Canonical hash of a petanque goal stack: the pretty-prints joined by a separator. Two states
    with the same open-goal stack are equivalent for the search, so this prunes revisits/cycles."""
    return "\n---\n".join(g.pretty for g in goals)


def _inv_name(invariant_src: str) -> str:
    import re

    m = re.search(r"Definition\s+(\w+)", invariant_src)
    return m.group(1) if m else "timing_invs"


def _unproved_goal_msg(goals) -> str:
    """Format the residual proof obligation for verifier-guided refinement: the goal the proof
    couldn't close (typically the `cycle = ...` equation), which the next synthesis attempt uses to
    correct the invariant. This is the verifier's goal state, not the spec's answer."""
    if not goals:
        return "repair budget exhausted (no residual goal captured)"
    g = goals[0]
    concl = (g.conclusion or g.pretty or "").strip()
    return (
        "the proof could not close this remaining goal — your invariant's timing/registers are "
        f"likely off here:\n{concl[:500]}"
    )
