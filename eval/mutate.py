"""Mutation / metamorphic testing — anti-vacuity layer #3.

A timing proof is only meaningful if it would *fail* on code whose timing actually changed. So we
inject a known leak/timing change, then require that the proof no longer goes through. FPGA
validation is parked (CLAUDE.md), so this layer is **proof-only**: `caught` means the proof broke.
An optional FPGA variance signal can still be supplied; when present and it *disagrees* with the
proof (the proof failed but no measured variance) the mutation is reported as uncaught.

Two flavours of mutation:
  * source/asm leaks (`LEAKS`) — make a branch/loop depend on the secret. These exercise the whole
    compile -> lift -> prove pipeline but need the caller to rebuild the target, so they are
    catalogued here and driven by the eval harness, not run inline.
  * cycle-form perturbations (`cycle_form_mutations`) — corrupt the invariant's `cycle_count_of_trace
    t' = …` closed form (drop/double a per-instruction term). These run inline against the prover
    with no rebuild: a sound discharge must REFUSE a wrong cycle count. This is what proves the
    array/pointer (ct_swap) close is constrained by the real timing, not vacuously true.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Mutation:
    name: str
    description: str
    apply: str             # human/repro description of the transform
    payload: str | None = None  # the mutated artifact (e.g. corrupted invariant source), when inline


# Canonical source-level timing leaks (need a rebuild; catalogued for the pipeline harness).
LEAKS: list[Mutation] = [
    Mutation("secret_branch",
             "Insert `if (secret & 1) nop_chain();` so one path is longer.",
             "add a secret-dependent conditional with unequal arms"),
    Mutation("secret_loop_bound",
             "Make a loop bound depend on the secret (popcount(secret) extra iterations).",
             "replace constant loop bound with secret-derived bound"),
    Mutation("secret_indexed_delay",
             "Add `for i in range(secret_byte): asm volatile(nop)`.",
             "secret-indexed delay loop"),
]


# Per-instruction timing tokens that appear in a Cloq cycle closed form; corrupting how many times
# one appears makes the predicted cycle count wrong without making the term ill-typed.
_CYCLE_TOKENS = ("tlw", "tsw", "taddi", "tadd", "tsub", "txor", "tand", "ttbne", "tjal", "tslli")


def cycle_form_mutations(invariant_src: str) -> list[Mutation]:
    """Generate proof-only mutations that corrupt the invariant's cycle closed form: for the first
    timing token present, one mutation that DOUBLES it and one that DROPS it. A correct discharge
    must fail to close either (the cycle equation becomes false), so these are the non-vacuity
    probes. Returns [] if no recognizable cycle token is found."""
    muts: list[Mutation] = []
    for tok in _CYCLE_TOKENS:
        # Match the token as a standalone summand `… + tok + …` (avoid tok-as-prefix, e.g. tadd⊂taddi).
        pat = re.compile(rf"(\+ )({re.escape(tok)})( \+)")
        if not pat.search(invariant_src):
            continue
        doubled = pat.sub(r"\1\2 + \2\3", invariant_src, count=1)
        dropped = pat.sub(r"\3", invariant_src, count=1)
        muts.append(Mutation(f"double_{tok}", f"double the `{tok}` cycle term",
                             f"+ {tok} + -> + {tok} + {tok} +", payload=doubled))
        muts.append(Mutation(f"drop_{tok}", f"drop a `{tok}` cycle term",
                             f"+ {tok} + -> +", payload=dropped))
        break
    return muts


@dataclass
class MutationOutcome:
    mutation: str
    proof_failed_as_expected: bool
    fpga_detected_variance: bool | None = None  # None = not measured (proof-only mode)

    @property
    def caught(self) -> bool:
        """Proof-only: caught iff the proof broke. If an FPGA signal was supplied it must not
        actively disagree (proof failed yet no measured variance => not a real catch)."""
        if not self.proof_failed_as_expected:
            return False
        return self.fpga_detected_variance is not False


def run_mutation_suite(
    target_name: str,
    *,
    prove_fn: Callable[[Mutation], bool],   # True if the mutated target STILL proves
    measure_variance_fn: Callable[[Mutation], bool] | None = None,  # None => proof-only
    mutations: list[Mutation] | None = None,
) -> list[MutationOutcome]:
    outcomes: list[MutationOutcome] = []
    for mut in mutations or LEAKS:
        still_proves = prove_fn(mut)
        variance = None if measure_variance_fn is None else measure_variance_fn(mut)
        outcomes.append(MutationOutcome(
            mutation=mut.name,
            proof_failed_as_expected=not still_proves,
            fpga_detected_variance=variance,
        ))
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
