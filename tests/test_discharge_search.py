"""DFS + backtracking _discharge closes a destruct-style fan-out that the old greedy loop can't.

The mock driver models goal stacks as tuples of goal-names; `finished` == empty stack. One tactic
('split2') fans 1 goal into 2 (the destruct_inv analogue), and the per-goal closers differ. The
first closer the proposer suggests for goal 'a' is a TRAP that applies cleanly (ok=True, makes
'progress') but leads to a dead end — so a greedy proof that commits to the first ok tactic gets
stuck, while the backtracking search pops back and takes the real closer.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cloq_agent.agent.tactic_repair as tr
from cloq_agent.agent.orchestrator import Orchestrator
from cloq_agent.config import Config
from cloq_agent.models import Completion
from cloq_agent.proof.petanque_driver import Goal, StepResult


def _goals(stack):
    return [Goal(pretty=g, hypotheses=[], conclusion=g) for g in stack]


class _MockDriver:
    """state == tuple of open goal-names; () means finished. Records every tactic run."""

    def __init__(self):
        self.tried: list[str] = []

    def _result(self, state, *, ok, error):
        return StepResult(ok=ok, finished=(len(state) == 0), goals=_goals(state),
                          error=error, state=state)

    def replay_from_root(self, file, theorem, path):  # never needed: handles stay live
        cur = self._result(("root",), ok=True, error=None)
        for tac in path:
            cur = self.run(cur.state, tac)
        return cur

    def run(self, state, tactic, timeout_s=None):
        self.tried.append(tactic)
        key = tactic.strip().rstrip(".")          # tactics arrive period-terminated from propose()
        head = state[0] if state else None
        rest = tuple(state[1:])
        nxt = None
        if head == "root" and key == "split2":
            nxt = ("a", "b") + rest               # the fan-out: 1 goal -> 2
        elif head == "a" and key == "closeA":
            nxt = rest                            # close goal 'a'
        elif head == "a" and key == "trapA":
            nxt = ("dead",) + rest                # ok, but a dead end
        elif head == "b" and key == "closeB":
            nxt = rest                            # close goal 'b'
        if nxt is None:                           # ladder rungs / prelude scripts: no progress
            return StepResult(ok=False, finished=False, goals=_goals(state),
                              error=f"no rule for {key!r} on {head!r}", state=state)
        return self._result(nxt, ok=True, error=None)


# Proposer output per goal-head (best-first); 'trapA' deliberately precedes 'closeA'.
_PROPOSALS = {
    "root": ["split2"],
    "a": ["trapA", "closeA"],
    "b": ["closeB"],
    "dead": ["nope"],
}


class _MockLLM:
    def complete(self, system, user, *, escalate=False) -> Completion:
        # tactic_repair.USER embeds the goal as `Open goal:\n{goal}\n` — match that anchor exactly.
        for head, tactics in _PROPOSALS.items():
            if f"Open goal:\n{head}\n" in user:
                text = "\n".join(f"{t}." for t in tactics)
                return Completion(text=text, model="mock", escalated=escalate)
        return Completion(text="idtac.", model="mock", escalated=escalate)

    @property
    def can_escalate(self) -> bool:
        return False


class _MockRetriever:
    def retrieve(self, query):
        class _R:
            def as_prompt_context(self_inner):
                return "(none)"
        return _R()


class _Spec:
    name = "fanout"
    theorem_name = "fanout_thm"


def _orchestrator() -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)   # bypass heavy __init__ (no embedder / API client)
    orch.cfg = Config()
    orch.llm = _MockLLM()
    orch.retriever = _MockRetriever()
    orch.workspace = Path("/tmp")
    orch.fpga_oracle = None
    return orch


def test_dfs_closes_fanout_with_backtracking():
    drv = _MockDriver()
    start = drv._result(("root",), ok=True, error=None)
    res = _orchestrator()._discharge(drv, start, _Spec(), attempt=1, llm_calls=0,
                                     escalated=False, t0=time.time())
    assert res.proved is True
    # The winning path fans out then closes BOTH subgoals with the right (non-trap) closers.
    assert res.proof_script == ["split2.", "closeA.", "closeB."]
    assert res.closing_tactic == "closeB."
    # Backtracking actually happened: the trap was tried and then abandoned.
    assert "trapA." in drv.tried


def test_deterministic_only_ablation_skips_llm_repair():
    """With `llm_repair_enabled=False`, _discharge runs the deterministic layer only: it makes ZERO
    tactic-repair LLM calls and so cannot close the fan-out (which needs a proposed splitter)."""
    calls = {"n": 0}

    def _counting_propose(llm, goal, retrieved, *, escalate=False, past_failures=frozenset()):
        calls["n"] += 1
        return ["split2."]

    drv = _MockDriver()
    start = drv._result(("root",), ok=True, error=None)
    orch = _orchestrator()
    orch.cfg.agent.llm_repair_enabled = False
    orig = tr.propose
    tr.propose = _counting_propose
    try:
        res = orch._discharge(drv, start, _Spec(), attempt=1, llm_calls=0,
                              escalated=False, t0=time.time())
    finally:
        tr.propose = orig
    assert res.proved is False
    assert res.llm_calls == 0
    assert calls["n"] == 0          # propose() never invoked in deterministic-only mode


def _greedy(drv, proposals) -> bool:
    """The OLD behavior: read goals[0], commit to the FIRST tactic that returns ok, discard the
    prior state, never backtrack. Demonstrates it cannot close the fan-out."""
    state = ("root",)
    for _ in range(20):
        if not state:
            return True
        head = state[0]
        committed = False
        for tac in proposals.get(head, []):
            step = drv.run(state, tac)
            if step.ok:
                state = step.state      # commit + discard prior state (no way back)
                committed = True
                break
        if not committed:
            return False
    return False


def test_greedy_old_behavior_fails_on_same_fanout():
    drv = _MockDriver()
    # Greedy commits to trapA (first ok candidate for 'a') and strands itself in 'dead'.
    assert _greedy(drv, _PROPOSALS) is False


def test_search_budget_exhaustion_returns_unproved_with_residual():
    """A fan-out with no closer for 'b' can't finish; the search exhausts and reports a residual
    obligation (fed back into the next synthesis attempt), not a crash."""
    drv = _MockDriver()
    start = drv._result(("root",), ok=True, error=None)
    orch = _orchestrator()
    orig = tr.propose

    def _propose(llm, goal, retrieved, *, escalate=False, past_failures=frozenset()):
        if goal.pretty == "b":
            return []                            # no way to close 'b'
        return orig(llm, goal, retrieved, escalate=escalate, past_failures=past_failures)

    tr.propose = _propose
    try:
        res = orch._discharge(drv, start, _Spec(), attempt=1, llm_calls=0,
                              escalated=False, t0=time.time())
    finally:
        tr.propose = orig
    assert res.proved is False
    assert res.error  # residual-goal message / budget note for verifier-guided refinement
    assert res.proof_script and res.proof_script[0] == "split2."  # deepest path tried


def test_failed_candidate_recorded_in_past_failures_set():
    """_discharge hands propose() the LIVE per-goal failures set and adds a failed candidate to it,
    so any re-proposal for that goal would exclude it (propose() filters past_failures)."""
    captured: dict[str, set] = {}

    def _spy(llm, goal, retrieved, *, escalate=False, past_failures=frozenset()):
        captured["set"] = past_failures        # the same set object _discharge mutates on failure
        return ["bogusA."]                       # a tactic the mock driver rejects

    drv = _MockDriver()
    start = drv._result(("a",), ok=True, error=None)  # start directly on goal 'a'
    orch = _orchestrator()
    orig = tr.propose
    tr.propose = _spy
    try:
        orch._discharge(drv, start, _Spec(), attempt=1, llm_calls=0,
                        escalated=False, t0=time.time())
    finally:
        tr.propose = orig
    assert isinstance(captured.get("set"), set)
    assert "bogusA." in captured["set"]          # recorded as a dead end for goal 'a'
