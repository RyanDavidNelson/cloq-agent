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
- A goal `exists _, _` for a loop counter: provide the witness explicitly. The loop counter is the
  register the body increments; if the previous iteration's index is `i` in the hypotheses, use
  `exists (1 + i).` (or `exists 0.` for zero iterations); `handle_ex.` discharges the trivial
  conjuncts after. Do NOT leave the existential to `eauto` alone.
- A modular subtraction `a ⊖ b` that doesn't wrap: `rewrite msub_nowrap by lia.` then continue.
- Distribute a nested subtraction `x - (a - b)`: `rewrite N_sub_distr; lia.`
- Split a conjunction goal with `repeat split.`; finish each with `assumption`/`reflexivity`."""

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
) -> list[str]:
    user = USER.format(
        goal=goal.conclusion or goal.pretty,
        hyps="\n".join(goal.hypotheses) or "(none)",
        context=retrieved.as_prompt_context(),
    )
    out = llm.complete(SYSTEM, user, escalate=escalate)
    return _parse(out.text)
