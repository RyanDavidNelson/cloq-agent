"""Invariant-set synthesis — the one genuinely creative step.

Two modes:

* **freeform** — the model emits the whole `Definition timing_invs ...` (match structure,
  addresses, every arm). Kept for A/B comparison.
* **skeleton** — the CFG emits the match scaffold, the invariant-point addresses, and the pinned
  postcondition arm; the model fills only the loop/entry holes (the closed-form `(c0 - c)*t` and
  the termination quantity). We parse the model output back into the skeleton and *reject* any
  output that alters an address, the match arms, or the pinned postcondition, then re-splice the
  hole bodies into the trusted scaffold so soundness holds by construction.

We return raw Rocq source; whether it's *correct* is decided downstream by petanque, never here.
"""
from __future__ import annotations

import logging
import re

from ..models import LLM
from ..lift.cfg import SkeletonPlan
from ..rag.retriever import Retrieved

log = logging.getLogger("cloq_agent.invariant_synth")

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

SYSTEM_SKELETON = """You are an expert in the Cloq timing-verification framework built on Picinae in Rocq/Coq.
You are given a timing-invariant skeleton whose match structure, invariant-point addresses, and
postcondition arm are FIXED. Your only job is to replace each `(* HOLE:0xADDR ... *) FILL_ME`
placeholder with a Coq proposition:
- for a loop header: the registers/bounds that hold there plus a closed form
  `cycle_count_of_trace t' = (c0 - c) * t_body (+ constants)`;
- for the entry: the precondition (register ties plus `cycle_count_of_trace t' = 0`).
Hard constraints:
- Reproduce EVERY `| 0xADDR => ...` arm with its address unchanged.
- Do NOT add, remove, or renumber any address.
- Reproduce the arm marked `(* PINNED ... *)` EXACTLY; never change the postcondition.
- Output ONLY the completed `Definition ... .`, no prose, no markdown fences."""

USER_SKELETON = """Target: {name}
Entry: 0x{entry:x}

Control-flow graph:
{cfg}

Skeleton to complete (keep all addresses and the PINNED arm exactly as given):
{skeleton}

{context}

Return the completed Definition with every hole filled."""

_DEF_RE = re.compile(r"(Definition\b.*?\.\s*$)", re.DOTALL | re.MULTILINE)
_ARM_OPEN = re.compile(r"\|\s*0x([0-9a-fA-F]+)\s*=>\s*Some\s*\(")
_COMMENT_RE = re.compile(r"\(\*.*?\*\)", re.DOTALL)


def _clean(text: str) -> str:
    text = re.sub(r"```[a-zA-Z]*", "", text).replace("```", "").strip()
    m = _DEF_RE.search(text)
    return (m.group(1) if m else text).strip()


def _norm(s: str) -> str:
    """Normalize for arm comparison: drop Coq comments, then collapse whitespace. The skeleton
    embeds a `(* PINNED ... *)` marker in the exit arm, and models faithfully echo it back inside
    the body — that is not an alteration of the proposition, so it must not trip the guard."""
    return re.sub(r"\s+", " ", _COMMENT_RE.sub(" ", s)).strip()


def _extract_arms(text: str) -> dict[int, str]:
    """Parse `| 0xADDR => Some (BODY)` arms from model output, balancing nested parens in BODY."""
    arms: dict[int, str] = {}
    for m in _ARM_OPEN.finditer(text):
        addr = int(m.group(1), 16)
        i = m.end()
        depth, j = 1, i
        while j < len(text) and depth:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
            j += 1
        arms[addr] = text[i:j - 1].strip()
    return arms


def _splice_skeleton(plan: SkeletonPlan, model_text: str) -> str | None:
    """Validate the model's completed Definition against the skeleton and re-splice the hole
    bodies into the trusted scaffold. Returns the filled Definition, or None to reject+retry.
    """
    arms = _extract_arms(model_text)
    expected = set(plan.hole_addrs) | set(plan.exit_addrs)
    if set(arms) != expected:
        log.warning(
            "skeleton rejected: model arm addresses %s != invariant points %s",
            sorted(hex(a) for a in arms), sorted(hex(a) for a in expected),
        )
        return None
    for a in plan.exit_addrs:
        if _norm(arms[a]) != _norm(plan.postcondition):
            log.warning("skeleton rejected: model altered the pinned postcondition at 0x%x", a)
            return None
    # Re-pin: take ONLY the model's hole bodies; addresses, scaffold and postcondition are ours.
    fills = {a: arms[a] for a in plan.hole_addrs}
    return plan.fill(fills)


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
    mode: str = "freeform",
    skeleton: SkeletonPlan | None = None,
) -> str:
    if mode == "skeleton" and skeleton is not None:
        user = USER_SKELETON.format(
            name=name, entry=entry, cfg=cfg_description,
            skeleton=skeleton.prompt_text, context=retrieved.as_prompt_context(),
        )
        out = llm.complete(SYSTEM_SKELETON, user, escalate=escalate, temperature=temperature)
        spliced = _splice_skeleton(skeleton, _clean(out.text))
        # On rejection, hand back the skeleton (holes unfilled) so the proof fails and the
        # orchestrator's attempt loop retries, rather than admitting a tampered invariant.
        return spliced if spliced is not None else skeleton.fill({})

    user = USER.format(
        name=name,
        entry=entry,
        cfg=cfg_description,
        context=retrieved.as_prompt_context(),
        inv_name=inv_name,
    )
    out = llm.complete(SYSTEM, user, escalate=escalate, temperature=temperature)
    return _clean(out.text)
