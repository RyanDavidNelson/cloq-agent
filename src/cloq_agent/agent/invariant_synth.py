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
{feedback}
Write the invariant set for this target. It MUST be exactly:
  Definition {inv_name} {signature}(t:trace) := match t with (Addr a, s) :: t' => match a with
  | <addr> => Some (<proposition>) | ... | _ => None end | _ => None end.
Use exactly these binders ({signature}t:trace) and the name `{inv_name}` — do NOT add an
address/program parameter. One arm per invariant point (entry, each loop header, each exit).
Output only the Definition."""

SYSTEM_SKELETON = """You are an expert in the Cloq timing-verification framework built on Picinae in Rocq/Coq.
You are given a timing-invariant skeleton whose match structure, invariant-point addresses, and
postcondition arm are FIXED. Your only job is to replace each `(* HOLE:0xADDR ... *) FILL_ME`
placeholder with a Coq proposition:
- for a loop header: the cycle_count timing is COMPUTED for you and shown in the hole as
  `cycle_count_of_trace t' = <pre> + (<counter>) * (<body>)` — USE those exact `<pre>`/`<body>`
  timing terms (don't change them). Your job is the rest of the arm: pick `<counter>` (the register
  the body increments, e.g. `(s R_A5)`, OR introduce an index with
  `exists i, ... i <= len ... s R_A2 = base ⊕ (4*i) ...` when the counter is only a pointer), and
  add the register/bound/memory facts that hold here (counter ≤ length, memory preserved, etc.).
- for the entry: the precondition — almost always exactly `cycle_count_of_trace t' = 0` (add a
  register tie only if a later arm needs it and it is given as an entry hypothesis).
Timing-constant rules (CRITICAL — wrong constants make the proof fail):
- Use ONLY the per-instruction constants matching the CFG's instructions: tlw (lw), tsw (sw),
  taddi (addi), tadd (add), tsub (sub), tslli (slli, written `tslli 2`), txor (xor), tand (and),
  tor (or), tjal (jal), tjalr (jalr).
- For a CONDITIONAL BRANCH there are TWO constants: taken `tt<op>` and fall-through `tf<op>`
  (e.g. ttbeq/tfbeq, ttbne/tfbne, ttbgeu/tfbgeu). NEVER invent a single `tbeq`/`tbne`/`tbgeu`.
- The loop body's branch is the fall-through case (the loop kept going), so use the `tf<op>` form.
Worked examples of loop-header arms (these are from OTHER programs — adapt the form to THIS one,
do not copy the registers/constants):
- counter that decreases from x (in R_T0) to 0:
  Some (s R_T2 = 1 /\\ s R_T0 <= x /\\
        cycle_count_of_trace t' = tori + tandi + (x - s R_T0) * (tfbeq + taddi + tsub + ttbeq))
- index counter i (in some R_Ai) rising 0..len, carrying a "not done yet" fact:
  Some (s R_A5 <= len /\\ (forall j, j < s R_A5 -> <fact about element j>) /\\
        cycle_count_of_trace t' = <pre> + (s R_A5) * (<one fall-through iteration body>))
- loop counter that exists only implicitly (a pointer p = base + 4*i): INTRODUCE the index with
  an existential and tie the pointer to it:
  Some (exists i, i <= len /\\ s R_A2 = base ⊕ (4 * i) /\\
        cycle_count_of_trace t' = <pre> + i * (<one fall-through iteration body>))
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
{feedback}
Return the completed Definition with every hole filled."""

_DEF_RE = re.compile(r"(Definition\b.*?\.\s*$)", re.DOTALL | re.MULTILINE)
_ARM_OPEN = re.compile(r"\|\s*0x([0-9a-fA-F]+)\s*=>\s*Some\s*\(")
_COMMENT_RE = re.compile(r"\(\*.*?\*\)", re.DOTALL)


def _clean(text: str) -> str:
    text = re.sub(r"```[a-zA-Z]*", "", text).replace("```", "").strip()
    m = _DEF_RE.search(text)
    return (m.group(1) if m else text).strip()


def _binders(params) -> str:
    """`(name : type) ...` for the spec params (trailing space if non-empty)."""
    s = " ".join(f"({p[0]} : {p[1]})" for p in (params or []))
    return (s + " ") if s else ""


# Header up to and including `(t:trace) [: <ret>] :=`, so we can re-pin it to the spec signature.
_HEADER_RE = re.compile(
    r"Definition\s+.*?\(\s*t\s*:\s*trace\s*\)\s*(?::\s*[^:=]*?)?:=", re.DOTALL
)


def _force_signature(text: str, inv_name: str, params) -> str:
    """Rewrite the model's `Definition <name> <binders> (t:trace) [: ..] :=` header to the spec's
    canonical name + binder list, dropping any spurious leading binder (e.g. `(p:addr)`) the model
    added. The arm bodies are untouched. This is the freeform-mode fix for invariants that were
    semantically right but mechanically rejected because the rendered `(inv_name args)` application
    didn't match the declared arity. If the header can't be located, the text is returned as-is."""
    if not params:
        return text
    header = f"Definition {inv_name} {_binders(params)}(t:trace) :="
    new, n = _HEADER_RE.subn(header, text, count=1)
    return new if n else text


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


def _feedback_block(feedback: str | None) -> str:
    """Render the previous attempt's failure (Rocq/lint error) as a corrective prompt segment."""
    if not feedback:
        return ""
    return (
        "\nYOUR PREVIOUS ATTEMPT FAILED. Fix it. The proof engine reported:\n"
        f"{feedback.strip()[:600]}\n"
    )


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
    params=None,
    feedback: str | None = None,
) -> str:
    if mode == "skeleton" and skeleton is not None:
        user = USER_SKELETON.format(
            name=name, entry=entry, cfg=cfg_description,
            skeleton=skeleton.prompt_text, context=retrieved.as_prompt_context(),
            feedback=_feedback_block(feedback),
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
        signature=_binders(params),
        feedback=_feedback_block(feedback),
    )
    out = llm.complete(SYSTEM, user, escalate=escalate, temperature=temperature)
    # Re-pin the header to the spec signature so a spurious leading binder (the common `(p:addr)`
    # failure) can't under-apply the rendered `(inv_name args)`.
    return _force_signature(_clean(out.text), inv_name, params)
