"""Mutation / metamorphic testing — anti-vacuity layer #3.

A constant-time proof is only meaningful if it would *fail* on code that actually leaks. So we
inject a known timing leak into the target (make a branch depend on the secret), then require
that BOTH:
  (a) the proof no longer goes through, and
  (b) the FPGA now measures secret-dependent cycle variance.
If either still "passes", the harness or spec was trivial, and we report it.

Mutations operate on the source/assembly of the target, not on Rocq, so they exercise the whole
lift -> prove -> measure pipeline the way a real leak would.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Mutation:
    name: str
    description: str
    apply: str    # a unified-diff or sed-style transform recorded for reproducibility


# A small catalogue of canonical timing leaks to inject into constant-time targets.
LEAKS: list[Mutation] = [
    Mutation(
        "secret_branch",
        "Insert `if (secret & 1) nop_chain();` so one path is longer.",
        "add a secret-dependent conditional with unequal arms",
    ),
    Mutation(
        "secret_loop_bound",
        "Make a loop bound depend on the secret (popcount(secret) extra iterations).",
        "replace constant loop bound with secret-derived bound",
    ),
    Mutation(
        "secret_indexed_delay",
        "Add `for i in range(secret_byte): asm volatile(nop)`.",
        "secret-indexed delay loop",
    ),
]


@dataclass
class MutationOutcome:
    mutation: str
    proof_failed_as_expected: bool
    fpga_detected_variance: bool

    @property
    def caught(self) -> bool:
        return self.proof_failed_as_expected and self.fpga_detected_variance


def run_mutation_suite(
    target_name: str,
    *,
    prove_fn,          # callable() -> bool : True if the (mutated) target still proves
    measure_variance_fn,  # callable() -> bool : True if FPGA sees secret-dependent variance
    mutations: list[Mutation] | None = None,
) -> list[MutationOutcome]:
    outcomes: list[MutationOutcome] = []
    for mut in mutations or LEAKS:
        # NOTE: callers are responsible for actually applying `mut` to the target's build before
        # invoking prove_fn / measure_variance_fn (kept injectable so this stays unit-testable).
        still_proves = prove_fn(mut)
        variance = measure_variance_fn(mut)
        outcomes.append(
            MutationOutcome(
                mutation=mut.name,
                proof_failed_as_expected=not still_proves,
                fpga_detected_variance=variance,
            )
        )
    return outcomes


def summarize(outcomes: list[MutationOutcome]) -> dict:
    total = len(outcomes)
    caught = sum(o.caught for o in outcomes)
    return {
        "total": total,
        "caught": caught,
        "non_triviality_rate": caught / total if total else 0.0,
        "uncaught": [o.mutation for o in outcomes if not o.caught],
    }
