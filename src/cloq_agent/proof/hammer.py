"""The automation ladder tried before spending any LLM tokens.

Cloq's own `whammer`/`hammer` close most timing goals (the paper's whole point), so they go
first. CoqHammer's `hammer`/`sauto` and the lia/eauto closers are the safety net. This is the
"hammer-first, LLM-fallback" pattern that consistently beats LLM-only proving.

Note the name clash: Cloq exports a tactic literally called `hammer`, and CoqHammer also
exports `hammer`. Whichever is imported last wins, so we reference Cloq's via `whammer` (its
higher-level wrapper) and reach CoqHammer through `sauto`/`hauto`/`qauto`, which are
unambiguous. Adjust `LADDER` to match your vendored Cloq tactic names.
"""
from __future__ import annotations

from dataclasses import dataclass

from .petanque_driver import PetanqueDriver, StepResult

# Ordered cheapest -> strongest. Each entry is a single tactic invocation.
LADDER: list[str] = [
    "whammer.",                 # Cloq: step; psimpl; lia bundle (closes most timing goals)
    "hammer.",                  # Cloq: lower-level variant making fewer assumptions
    "psimpl; lia.",             # binary-arith simplify then linear integer arithmetic
    "lia.",
    "now eauto with arith.",
    "sauto.",                   # CoqHammer
    "qauto.",                   # CoqHammer
]


@dataclass
class HammerOutcome:
    closed: bool
    tactic: str | None          # the tactic that closed it (for the proof script + RAG label)
    state: object | None


def try_ladder(
    driver: PetanqueDriver,
    state: object,
    ladder: list[str] | None = None,
) -> HammerOutcome:
    """Try each tactic on the current goal in order; stop at the first that finishes the proof.

    We attempt each tactic from the *same* incoming state. petanque is functional (each run
    returns a fresh state), so a failed attempt does not corrupt the goal we retry from.
    """
    for tac in ladder or LADDER:
        res: StepResult = driver.run(state, tac)
        if res.ok and res.finished:
            return HammerOutcome(closed=True, tactic=tac, state=res.state)
    return HammerOutcome(closed=False, tactic=None, state=state)
