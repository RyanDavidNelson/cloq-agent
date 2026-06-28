"""propose() feeds the backtracking search: ranked candidates, past failures excluded,
branch-point goals biased toward the Picinae case-splitters (Task 5).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.agent import tactic_repair
from cloq_agent.models import Completion
from cloq_agent.proof.petanque_driver import Goal


class _FakeLLM:
    """Returns canned text and records the prompts it was handed."""

    def __init__(self, text: str):
        self.text = text
        self.system: str | None = None
        self.user: str | None = None

    def complete(self, system: str, user: str, *, escalate: bool = False) -> Completion:
        self.system, self.user = system, user
        return Completion(text=self.text, model="fake", escalated=escalate)

    @property
    def can_escalate(self) -> bool:
        return False


class _FakeRetrieved:
    def as_prompt_context(self) -> str:
        return "(retrieved lemmas / prior proofs)"


def _goal(concl: str, hyps: list[str] | None = None) -> Goal:
    return Goal(pretty=concl, hypotheses=hyps or [], conclusion=concl)


def test_returns_full_ranked_list_in_order():
    llm = _FakeLLM("destruct_inv 32 PRE.\nrepeat step; hammer.\nlia.\n")
    out = tactic_repair.propose(llm, _goal("some goal"), _FakeRetrieved())
    assert out == ["destruct_inv 32 PRE.", "repeat step; hammer.", "lia."]


def test_past_failures_are_excluded():
    llm = _FakeLLM(
        "destruct_inv 32 PRE.\nhammer.\nrepeat step; hammer.\nlia.\npsimpl; lia.\n"
    )
    out = tactic_repair.propose(
        llm, _goal("some goal"), _FakeRetrieved(),
        past_failures={"destruct_inv 32 PRE.", "hammer."},
    )
    assert "destruct_inv 32 PRE." not in out
    assert "hammer." not in out
    # The survivors keep their ranked order.
    assert out == ["repeat step; hammer.", "lia.", "psimpl; lia."]


def test_branch_point_goal_yields_destruct_class_candidate():
    """A branch-point goal (trace head + BC hypothesis + invariant conjunction) should draw a
    `destruct`-class splitter. The SYSTEM prompt must carry that bias for the real model."""
    branch_goal = _goal(
        "satisfies_all lifted_prog inv exits ((Addr a, s) :: t')",
        hyps=["BC : (s R_T0 =? 0) = false", "PRE : s R_T0 <= x /\\ cycle_count_of_trace t' = ..."],
    )
    # A model that follows the strengthened prompt proposes splitters first.
    llm = _FakeLLM("destruct_inv 32 PRE.\ndestruct PRE as (T0 & Cyc).\npreserve_noverlaps.\n")
    out = tactic_repair.propose(llm, branch_goal, _FakeRetrieved())

    assert any(t.startswith("destruct") for t in out)
    # The bias is actually in the system prompt handed to the LLM (so a real model is steered).
    assert "destruct_inv 32 PRE." in llm.system
    assert "destruct PRE as (...)." in llm.system
    assert "BC" in llm.system
    # T4 memory-aliasing closers are named for branch points.
    assert "preserve_noverlaps" in llm.system
    assert "getmem_noverlap" in llm.system


def test_system_prompt_carries_loop_worked_exemplar():
    """The prompt ships the exact vendored loop-counter closing sequence (msub_nowrap / N_sub_distr)
    — the residual class the deterministic loop ladder targets — with the ordering caveat."""
    s = tactic_repair.SYSTEM
    assert "rewrite msub_nowrap by (psimpl; lia)." in s
    assert "rewrite N_sub_distr; lia." in s
    assert "before `psimpl`" in s
    # The aliasing closers are flagged as possibly out-of-scope (program-specific Local Ltac),
    # not presented as a copy-me worked example.
    assert "program-specific" in s


def test_generator_only_no_period_lines_dropped():
    """propose() only suggests well-formed tactic lines (period-terminated); prose is ignored."""
    llm = _FakeLLM("Here is my plan:\ndestruct_inv 32 PRE.\n(no period here)\nlia.\n")
    out = tactic_repair.propose(llm, _goal("g"), _FakeRetrieved())
    assert out == ["destruct_inv 32 PRE.", "lia."]
