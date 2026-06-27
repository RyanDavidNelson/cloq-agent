"""Invariant-set synthesis — the one genuinely creative step.

Given the CFG and retrieved analogues, ask the model for a Cloq `timing_invs` definition: one
invariant per loop header (a closed-form cycle-count expression `(c0 - c) * t` plus the loop's
termination quantity), a precondition, and a postcondition `t0 + c0 * t`. We return raw Rocq
source; whether it's *correct* is decided downstream by petanque, never by the LLM.
"""
from __future__ import annotations

import re

from ..models import LLM
from ..rag.retriever import Retrieved

SYSTEM = """You are an expert in the Cloq timing-verification framework built on Picinae in Rocq/Coq.
You write timing invariant sets for lifted machine code. A timing invariant set is a Coq
`Definition timing_invs (p:addr) (...) (t:trace) := match t with (Addr a, s) :: t' => match a with
| <addr> => Some (<proposition about cycle_count t' and registers>) | ... | _ => None end | _ => None end.`
Rules:
- One arm per loop header and one for the exit/post-condition.
- Loop invariants express cycle_count as a closed form in the loop counter, e.g. (c0 - c) * t_body.
- The constant-time obligation, when relevant, asserts cycle_count does NOT depend on the secret.
- Output ONLY a single Coq Definition, no prose, no markdown fences."""

USER = """Target: {name}
Entry: 0x{entry:x}

Control-flow graph:
{cfg}

{context}

Write the `Definition {inv_name} ...` invariant set for this target. Output only the Definition."""

_DEF_RE = re.compile(r"(Definition\b.*?\.\s*$)", re.DOTALL | re.MULTILINE)


def _clean(text: str) -> str:
    text = re.sub(r"```[a-zA-Z]*", "", text).replace("```", "").strip()
    m = _DEF_RE.search(text)
    return (m.group(1) if m else text).strip()


def synthesize(
    llm: LLM,
    *,
    name: str,
    entry: int,
    cfg_description: str,
    retrieved: Retrieved,
    inv_name: str = "timing_invs",
    escalate: bool = False,
    temperature: float = 0.4,
) -> str:
    user = USER.format(
        name=name,
        entry=entry,
        cfg=cfg_description,
        context=retrieved.as_prompt_context(),
        inv_name=inv_name,
    )
    out = llm.complete(SYSTEM, user, escalate=escalate, temperature=temperature)
    return _clean(out.text)
