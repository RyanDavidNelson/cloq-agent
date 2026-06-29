"""Premise-satisfiability gate — the load-bearing integrity check that replaces FPGA's implicit
cross-check.

Every past-ceiling theorem carries *assumed* well-formedness premises (ct_swap's `PTR_ALIGN`
`exists k', base = 4*k'` and `LEN_VALID` `4*len < 2^32`; later `noverlaps`). A theorem whose
premises are jointly UNSATISFIABLE is vacuously true — it "proves" the timing bound only because it
assumes something impossible. Mutation (`eval/mutate.py`) catches a vacuous *postcondition*; it does
NOT catch a vacuous *premise*. FPGA used to catch this implicitly (you can't measure a board state
that can't exist). With FPGA parked, this is the explicit gate: for the conjunction of the
input-well-formedness premises we emit `exists <binders>, <premises>` and require Rocq to discharge
it. A contradictory premise then fails at GENERATION time instead of yielding a sound-looking proof
of nothing.

Scope: only the premises that constrain the *inputs* (they do not read the machine state `s`) — the
ones a synthesized/specified spec could make contradictory. State ties (`s R_A3 = len`) are
tautologically satisfiable (a store is a free function) and are not the vacuity risk.
"""
from __future__ import annotations

import re

# Discharge for the satisfiability obligation: witness every existential at 0 and interleave with
# `split`, then close the arithmetic leaves. Sufficient for the corpus's alignment/bound premises
# (all satisfiable at 0). A premise that is only satisfiable at a NON-zero witness (e.g. `len > 0`)
# would conservatively fail here — soundness-safe (it rejects, never wrongly accepts a vacuous one).
PREMISE_DISCHARGE = (
    "repeat (first [ split | match goal with |- exists _, _ => exists 0 end ]); "
    "(try reflexivity); (try lia); (try (psimpl; lia))."
)


def _reads_state(prop: str) -> bool:
    """True if the premise reads the machine store `s` (`s R_…` / `s V_…`) — a state tie, not an
    input well-formedness constraint, so it is tautologically satisfiable and out of scope here."""
    return bool(re.search(r"\bs [A-Z]", prop))


def input_premises(spec) -> list[tuple[str, str]]:
    """The `entry_hyps` that constrain the inputs (do not read state) — the vacuity-risk set."""
    return [(n, p) for n, p in getattr(spec, "entry_hyps", []) if not _reads_state(p)]


def premise_obligation(spec) -> str | None:
    """A standalone Rocq lemma asserting the input premises are jointly satisfiable, or None when
    the target has no input premises (nothing to check). Quantifies only over the binders the
    premises actually mention, typed from `params`/`extra_binders`, so it stays in `NArith`."""
    prems = input_premises(spec)
    if not prems:
        return None
    text = " ".join(p for _, p in prems)
    binders = [(b[0], b[1]) for b in (list(spec.params) + list(spec.extra_binders))
               if re.search(rf"\b{re.escape(b[0])}\b", text)]
    bnd = " ".join(f"({n} : {t})" for n, t in binders)
    conj = " /\\ ".join(f"({p})" for _, p in prems)
    return (
        "(* AUTO-GENERATED premise-satisfiability obligation (anti-vacuity). *)\n"
        "Require Import NArith. Require Import Picinae_riscv. Require Import Lia.\n"
        "Import RISCVNotations.\nOpen Scope N_scope.\n"
        f"Lemma premise_sat : exists {bnd}, {conj}.\nProof.\n  {PREMISE_DISCHARGE}\nQed.\n"
    )


def check_premises_satisfiable(driver, spec, workspace) -> tuple[bool, str | None]:
    """Emit the obligation and require Rocq to discharge it. Returns (ok, error):
      * (True, None)  — no input premises, or they are jointly satisfiable;
      * (False, msg)  — the premises are UNSATISFIABLE (vacuous theorem) or could not be elaborated.
    `driver` is a live PetanqueDriver; `workspace` the Rocq workspace Path."""
    from pathlib import Path

    src = premise_obligation(spec)
    if src is None:
        return True, None
    path = Path(workspace) / "targets" / f"{spec.name.capitalize()}_premises.v"
    path.write_text(src)
    try:
        start = driver.start(f"{workspace}/targets/{path.name}", "premise_sat")
        if not start.ok:
            return False, f"premise obligation did not elaborate: {start.error}"
        out = driver.run(start.state, PREMISE_DISCHARGE)
        if out.finished:
            return True, None
        prems = "; ".join(f"{n}: {p}" for n, p in input_premises(spec))
        return False, (f"premises are not jointly satisfiable (theorem would be vacuously true): "
                       f"{prems}")
    finally:
        path.unlink(missing_ok=True)
