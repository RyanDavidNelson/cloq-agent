"""Mutation / anti-vacuity tests. The unit part (server-free) covers the proof-only harness; the
integration part (real pet-server) proves the ct_swap array/pointer close is NON-VACUOUS: every
corruption of its cycle closed form must fail to discharge."""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.config import load_config

from eval.mutate import (
    MutationOutcome,
    cycle_form_mutations,
    run_mutation_suite,
    summarize,
)

_REPO = Path(__file__).resolve().parents[1]
_CFG = load_config()
_TARGETS = str(_REPO / "eval" / "targets.yaml")

_INV = (
    "Definition f_timing_invs (len : N) (t:trace) : option Prop := "
    "cycle_count_of_trace t' = tslli 2 + tsub + tadd + len * (ttbne + tlw + tlw + taddi)."
)


# --- unit: proof-only harness (no server) ------------------------------------

def test_cycle_form_mutations_corrupt_a_real_token():
    muts = cycle_form_mutations(_INV)
    assert muts, "expected at least one cycle-form mutation"
    names = {m.name for m in muts}
    assert any(n.startswith("double_") for n in names)
    assert any(n.startswith("drop_") for n in names)
    for m in muts:
        assert m.payload is not None and m.payload != _INV  # the corruption actually changed it


def test_caught_is_proof_only_by_default():
    # Proof broke, FPGA not measured (None) -> caught.
    assert MutationOutcome("m", proof_failed_as_expected=True).caught is True
    # Proof still went through -> never caught.
    assert MutationOutcome("m", proof_failed_as_expected=False).caught is False
    # Proof broke but a supplied FPGA signal disagrees (no variance) -> not a real catch.
    assert MutationOutcome("m", proof_failed_as_expected=True,
                           fpga_detected_variance=False).caught is False


def test_run_mutation_suite_proof_only_counts_breaks():
    muts = cycle_form_mutations(_INV)
    # prove_fn: pretend every corrupted invariant fails to prove (the desired behaviour).
    outcomes = run_mutation_suite("f", prove_fn=lambda _m: False, mutations=muts)
    s = summarize(outcomes)
    assert s["total"] == len(muts) and s["caught"] == len(muts)
    assert s["non_triviality_rate"] == 1.0 and s["uncaught"] == []


# --- integration: ct_swap non-vacuity (real pet-server) ----------------------

def _pet_server_up() -> bool:
    try:
        with socket.create_connection((_CFG.petanque.host, _CFG.petanque.port), timeout=1.0):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _pet_server_up(),
                    reason=f"no pet-server at {_CFG.petanque.host}:{_CFG.petanque.port}")
def test_ct_swap_close_is_non_vacuous():
    """The gold cycle form closes via the generic discharge; EVERY corrupted cycle form must not.
    If a wrong cycle count still closed, the array/pointer close would be vacuous."""
    from cloq_agent.lift.intake import loop_proof
    from cloq_agent.proof.hammer import run_script
    from cloq_agent.proof.petanque_driver import PetanqueDriver
    from cloq_agent.proof.theorem_builder import render, write

    from eval.replay import _inv_name
    from eval.targets import build_spec, load_targets

    t = load_targets(_TARGETS)["ct_swap"]
    spec, _d, _s, gold_inv, _gp, _sk = build_spec(t, _REPO, name="ct_swap")

    def closes(inv: str) -> bool:
        write(spec, render(spec, inv, _inv_name(inv)), Path(_CFG.petanque.workspace))
        with PetanqueDriver(_CFG.petanque, default_timeout_s=_CFG.agent.tactic_timeout_s) as d:
            start = d.start(
                f"{_CFG.petanque.workspace}/targets/{spec.name.capitalize()}_gen.v",
                spec.theorem_name)
            if not start.ok:
                return False
            return run_script(d, start.state, loop_proof(spec.addr_width)).closed

    assert closes(gold_inv), "baseline gold cycle form should close"

    muts = cycle_form_mutations(gold_inv)
    assert muts, "expected cycle-form mutations for ct_swap"
    outcomes = run_mutation_suite(
        "ct_swap", prove_fn=lambda m: closes(m.payload), mutations=muts)
    s = summarize(outcomes)
    assert s["uncaught"] == [], f"a corrupted cycle form still closed (vacuous!): {s['uncaught']}"
    assert s["caught"] == len(muts)
