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
