"""The automation ladder tried before spending any LLM tokens.

Cloq's `hammer` closes most timing *leaf* goals. CoqHammer's sauto/qauto and lia/eauto are
the safety net. NOTE: this vendored Cloq has no `whammer` wrapper, and no single tactic closes
a top-level `satisfies_all` goal — that needs the structured prove_invs/step/hammer script.
The ladder here closes residual *leaf* goals only.
"""
from __future__ import annotations

from dataclasses import dataclass

from .petanque_driver import PetanqueDriver, StepResult

LADDER: list[str] = [
    "hammer.",                  # Cloq leaf closer (was `whammer.` — that name does not exist)
    "psimpl; lia.",
    "lia.",
    "now eauto with arith.",
    "sauto.",                   # CoqHammer
    "qauto.",                   # CoqHammer
]


@dataclass
class HammerOutcome:
    closed: bool
    tactic: str | None
    state: object | None


# The generic Cloq timing-proof skeleton. A timing proof's structure is isomorphic to the CFG and
# is almost entirely mechanical: `apply prove_invs`, discharge the base case, set up the inductive
# step, `destruct_inv`, then `repeat step; hammer` each invariant arm. The *only* creative input is
# the invariant set (synthesized separately). These candidates differ only in how the entry-arm
# (base case) and the per-arm goals are closed, covering straight-line and conjunctive/▵ invariants.
# Tried from the fresh start state before spending LLM tokens on tactic repair.
_INDUCTIVE_SETUP = [
    "intros.",
    "eapply startof_prefix in ENTRY; try eassumption.",
    "eapply preservation_exec_prog in MDL; try eassumption; [idtac|apply lift_riscv_welltyped].",
    "clear - ENTRY PRE MDL. rename t1 into t. rename s1 into s'.",
    "destruct_inv 32 PRE.",
]
STRUCTURED_SCRIPTS: list[list[str]] = [
    # Straight-line / simple entry arm: `now step` closes the base case.
    ["intros.", "apply prove_invs.",
     "simpl. rewrite ENTRY. unfold entry_addr. now (tstep r5_step).",
     *_INDUCTIVE_SETUP,
     "all: (repeat (tstep r5_step); hammer)."],
    # Conjunctive entry arm (register ties etc.): split + assumption/arith before stepping arms.
    ["intros.", "apply prove_invs.",
     "simpl. rewrite ENTRY. unfold entry_addr. repeat (tstep r5_step). "
     "now (repeat split; (try assumption); (try reflexivity); (try (psimpl; lia)); (try hammer)).",
     *_INDUCTIVE_SETUP,
     "all: (repeat (tstep r5_step); repeat split; "
     "(try assumption); (try lia); (try (psimpl; lia)); (try hammer))."],
]


def try_structured(
    driver: PetanqueDriver, start_state: object, extra_scripts: list[list[str]] | None = None,
) -> HammerOutcome:
    """Try the generic structured Cloq proof, then any reusable proof scripts from the skill
    library, from the fresh start state. Returns closed=True iff a candidate finishes the whole
    `satisfies_all` goal. Petanque states are immutable, so each candidate runs from `start_state`.

    `extra_scripts` are previously-proven proof scripts (the gold proofs collected from solved
    targets): a synthesized invariant whose arm structure matches a solved target's is discharged
    by reusing that target's script — proof-skill transfer, with no LLM tokens. Scripts that don't
    fit the goal fail fast in `run_script` and are skipped, so trying the whole library is safe.
    """
    for script in [*STRUCTURED_SCRIPTS, *(extra_scripts or [])]:
        outcome = run_script(driver, start_state, script)
        if outcome.closed:
            return outcome
    return HammerOutcome(closed=False, tactic=None, state=start_state)


def try_ladder(
    driver: PetanqueDriver,
    state: object,
    ladder: list[str] | None = None,
) -> HammerOutcome:
    for tac in ladder or LADDER:
        res: StepResult = driver.run(state, tac)
        if res.ok and res.finished:
            return HammerOutcome(closed=True, tactic=tac, state=res.state)
    return HammerOutcome(closed=False, tactic=None, state=state)


def run_script(driver: PetanqueDriver, state: object, script: list[str]) -> HammerOutcome:
    """Run a fixed tactic script (the gold proof for a smoke target) step-by-step.
    Returns closed=True iff the proof finishes. Used to drive a known-good proof deterministically,
    with no LLM in the loop."""
    cur = driver._result(state, ok=True, error=None)
    last = None
    for tac in script:
        res = driver.run(cur.state, tac)
        if not res.ok:
            return HammerOutcome(closed=False, tactic=tac, state=cur.state)
        last, cur = tac, res
        if res.finished:
            return HammerOutcome(closed=True, tactic=last, state=res.state)
    return HammerOutcome(closed=bool(cur.finished), tactic=last, state=cur.state)
