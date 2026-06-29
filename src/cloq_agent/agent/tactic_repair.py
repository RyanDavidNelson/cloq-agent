"""Tactic repair for residual goals the hammer ladder couldn't close.

Given the pretty-printed goal (hypotheses + conclusion) and retrieved lemmas, the model
proposes a short list of candidate tactics to try next. We parse them line-by-line and the
orchestrator applies them through petanque, keeping whatever makes progress.
"""
from __future__ import annotations

import re

from ..models import LLM
from ..rag.retriever import Retrieved
from ..proof.petanque_driver import Goal

SYSTEM = """You are a Rocq/Coq proof engineer working inside the Picinae/Cloq timing framework.
You are given a single open goal and relevant lemmas. Propose up to 5 candidate next tactics,
most promising first, ONE per line, each ending in a period. No prose, no numbering, no markdown.

The Cloq timing idioms (use these — the leaf closer is `hammer`, there is NO `whammer`):
- Step one machine instruction: `tstep r5_step.`  (repeat with `repeat (tstep r5_step).`)
- Close a timing/arith leaf goal: `hammer.`  then `psimpl; lia.`  then `lia.`
- A loop invariant with an index `exists index, ...`: run `handle_ex.` FIRST — it clears the
  trivial alignment existentials (e.g. `exists k', base = 4 * k'`) — and THEN provide the index
  witness explicitly: `exists (1 + i).` where `i` is the previous iteration's index from the
  hypotheses (the var carrying an `i <= len` bound), or `exists 0.` for the zero-iteration base
  arm. Order matters: `handle_ex` before the explicit `exists`. Do NOT leave the index existential
  to a deferred `eexists`/`eauto` — it never unifies once the later splits constrain the cycle
  count.
- A modular subtraction `a ⊖ b` that doesn't wrap: `rewrite msub_nowrap by lia.` then continue.
- Distribute a nested subtraction `x - (a - b)`: `rewrite N_sub_distr; lia.`
- Split a conjunction goal with `repeat split.`; finish each with `assumption`/`reflexivity`.

When the goal is an INVARIANT or BRANCH POINT — it shows a trace `(Addr a, s) :: t'`, carries a
`BC` branch-condition hypothesis, or PRE is still an unbroken invariant conjunction — PREFER the
concrete Picinae case-splitters and per-branch closers, most promising first:
- Case-split the invariant set by program point: `destruct_inv 32 PRE.`
- Unpack the invariant conjunction in PRE: `destruct PRE as (...).`
- Split on a register / branch value: `destruct (<reg> ...) eqn:?.`
- Per-branch closers for the memory-aliasing residuals: `split.`, `preserve_noverlaps.`,
  `unfold_noverlap.`, `getmem_noverlap ...`, `noverlap_symmetry`, `find_rewrites.`, `lia.`
A splitter that fans the goal into the per-program-point / taken-vs-not-taken subgoals is usually
the right move here; do not try to close the whole conjunction with a single leaf tactic.

Worked example (the exact closing sequence from the vendored addloop proof — imitate this for a
loop-counter residual like `... = tori + tandi + (x - s R_T0) * (tfbeq + ...)` carrying a modular
subtraction `x ⊖ s R_T0` from the loop body): unpack, step, rewrite the no-wrap subtraction BEFORE
any psimpl/hammer normalizes it, then close, distributing the nested subtraction.
destruct PRE as (T2 & T3 & T0 & Cyc).
repeat (tstep r5_step).
rewrite msub_nowrap by (psimpl; lia).
repeat split.
lia.
hammer.
rewrite N_sub_distr; lia.

Order matters: `rewrite msub_nowrap` only matches the `⊖` notation, so apply it before `psimpl`
or `hammer` (which rewrite `⊖` into `mod` form and block the match).

(For memory-aliasing residuals the vendored closers `preserve_noverlaps`/`unfold_noverlap` are
program-specific and may be out of scope — fall back to `destruct`/`getmem_noverlap`/`hammer`.)"""

USER = """Open goal:
{goal}

Hypotheses:
{hyps}

{context}

Propose up to 5 candidate tactics, one per line."""

_TAC_LINE = re.compile(r"^\s*([A-Za-z].*\.)\s*$")


def _parse(text: str) -> list[str]:
    text = re.sub(r"```[a-zA-Z]*", "", text).replace("```", "")
    tactics: list[str] = []
    for line in text.splitlines():
        m = _TAC_LINE.match(line.strip())
        if m:
            tactics.append(m.group(1).strip())
    return tactics[:5]


def propose(
    llm: LLM,
    goal: Goal,
    retrieved: Retrieved,
    *,
    escalate: bool = False,
    past_failures: set[str] = frozenset(),
) -> list[str]:
    """Return the model's ranked candidate tactics for `goal`, most promising first.

    `past_failures` are tactics already known to fail on THIS goal (collected by the backtracking
    search across attempts at the same node); they are filtered out so the search never re-proposes
    a dead end. propose() only *suggests* — the search runs each candidate and adjudicates."""
    user = USER.format(
        goal=goal.conclusion or goal.pretty,
        hyps="\n".join(goal.hypotheses) or "(none)",
        context=retrieved.as_prompt_context(),
    )
    out = llm.complete(SYSTEM, user, escalate=escalate)
    return [t for t in _parse(out.text) if t not in past_failures]
