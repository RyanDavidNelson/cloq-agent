"""The automation ladder tried before spending any LLM tokens.

Cloq's `hammer` closes most timing *leaf* goals. CoqHammer's sauto/qauto and lia/eauto are
the safety net. NOTE: this vendored Cloq has no `whammer` wrapper, and no single tactic closes
a top-level `satisfies_all` goal — that needs the structured prove_invs/step/hammer script.
The ladder here closes residual *leaf* goals only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .petanque_driver import PetanqueDriver, StepResult

# Unpacking the invariant conjunction (PRE) ahead of stepping is the move the vendored LOOP arms
# open with — `destruct PRE as [...]`. This generic-arity form peels each leading conjunct, keeping
# the name PRE for the tail, and stops (via the bounded `repeat`) when PRE is no longer a `/\`.
_PRE_UNPACK = "(try (repeat (destruct PRE as [? PRE])))"

# The deterministic LOOP-arithmetic arm (CLAUDE.md T3/loop): unpack PRE, drive the instructions
# (which branch the loop), then close the modular-counter obligations the generic ladder can't —
# `rewrite msub_nowrap by (psimpl; lia)` (a ⊖ b that doesn't wrap) and `rewrite N_sub_distr; lia`
# (distribute x - (a - b)), interleaved with split/assumption/lia/hammer. Mirrors the vendored
# riscv_addloop loop arm. Wrapped in `try`s so it is safe on the branch where a rewrite doesn't fire.
_LOOP_ARM = (
    f"{_PRE_UNPACK}; repeat (tstep r5_step); "
    "(try (rewrite msub_nowrap by (psimpl; lia))); "
    "repeat split; (try assumption); (try reflexivity); (try lia); (try (psimpl; lia)); "
    "(try (hammer; (try (rewrite N_sub_distr; lia)))); "
    "(try (rewrite N_sub_distr; lia)); (try hammer)"
)

# NOTE on memory-aliasing (T4): a deterministic noverlap closer is NOT included here because its
# load-bearing tactics — `preserve_noverlaps`, `unfold_noverlap` — are program-specific `Local Ltac`
# defined *inside* each vendored proof body (they unfold THAT program's `memory_regions`/`noverlaps`
# definitions). They are out of scope in our generated theorem, so any such rung would be a
# try-skipped no-op. Real aliasing support needs `theorem_builder` to EMIT a program-specific
# `unfold_noverlap`/`preserve_noverlaps` from the target's memory structure — tracked as future work.

# Cheapest / most-bounded rung first. The `repeat step; …` workhorses (CLAUDE.md T2) drive the
# remaining machine instructions then close the timing leaf, so they sit ahead of the generic
# closers; `now step.` (entry-arm style) is the cheapest. The two LOOP rungs come last among the
# step-driven ones: they are the most expensive (unpack + branch + modular-arith rewrites), so try
# them only after the simpler workhorses miss. Each rung is bounded by the per-tactic timeout
# (Task 2), so a hung `repeat step`/`psimpl` is just a skipped rung, not a stall.
LADDER: list[str] = [
    "now step.",                # entry / single-instruction arm
    "repeat step; hammer.",     # workhorse: drive the arm, then the Cloq leaf closer
    "repeat step; psimpl; hammer.",
    "repeat step; psimpl; lia.",
    f"now ({_LOOP_ARM}).",      # loop arm, single focused goal (modular-counter arithmetic)
    f"all: ({_LOOP_ARM}).",     # loop arm applied across a whole taken/not-taken fan-out
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
    # Open goals when the script stalled (closed=False). Carries the residual proof obligation
    # (e.g. the unproven `cycle = ...` equation) so the orchestrator can resume repair there and
    # feed the mismatch back into the next synthesis attempt (verifier-guided refinement).
    residual: list = field(default_factory=list)


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
    # LOOP arm: same setup, but discharge each arm with the modular-counter arithmetic closer
    # (`msub_nowrap` / `N_sub_distr`) the generic `repeat step; hammer` can't. For targets whose
    # inductive arms carry a loop-counter cycle equation (addloop-class). Tried last; fails fast
    # and is skipped on targets it doesn't fit.
    ["intros.", "apply prove_invs.",
     "simpl. rewrite ENTRY. unfold entry_addr. repeat (tstep r5_step). "
     "now (repeat split; (try assumption); (try reflexivity); (try (psimpl; lia)); (try hammer)).",
     *_INDUCTIVE_SETUP,
     f"all: ({_LOOP_ARM})."],
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
    best: HammerOutcome | None = None
    for script in [*STRUCTURED_SCRIPTS, *(extra_scripts or [])]:
        outcome = run_script(driver, start_state, script)
        if outcome.closed:
            return outcome
        # Keep the candidate that progressed furthest (fewest residual goals) so the orchestrator
        # can resume repair from there and report the actual stuck obligation.
        if best is None or len(outcome.residual) < len(best.residual):
            best = outcome
    return best or HammerOutcome(closed=False, tactic=None, state=start_state)


def try_ladder(
    driver: PetanqueDriver,
    state: object,
    ladder: list[str] | None = None,
    timeout_s: float | None = None,
) -> HammerOutcome:
    """Run the ladder rung-by-rung from `state`, returning the first rung that finishes the goal.
    Each rung is bounded by the per-tactic timeout (`timeout_s`, else the driver default): a rung
    that times out or errors comes back `ok=False`/not-finished and is simply skipped to the next."""
    for tac in ladder or LADDER:
        res: StepResult = driver.run(state, tac, timeout_s)
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
            # Stalled: hand back the pre-failure state and its open goals (the residual obligation).
            return HammerOutcome(closed=False, tactic=tac, state=cur.state, residual=cur.goals)
        last, cur = tac, res
        if res.finished:
            return HammerOutcome(closed=True, tactic=last, state=res.state)
    return HammerOutcome(closed=bool(cur.finished), tactic=last, state=cur.state,
                         residual=[] if cur.finished else cur.goals)
