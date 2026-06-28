"""Theorem rendering produces a well-formed, parametrized timing theorem.

Two fixtures: the addloop smoke target (defaults) and a second, distinct program (CT-style,
different requires / program module / register convention) to prove the template is no longer
hardcoded to addloop.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.proof.theorem_builder import TargetSpec, render

SPEC = TargetSpec(
    name="addloop",
    requires=["Picinae_riscv", "TimingAutomation"],
    lifted_program="addloop",
    entry_addr=0,
    exit_point="addloop_exit",
    theorem_name="addloop_timing",
    params=[("x", "N"), ("y", "N")],
)

INV = "Definition timing_invs (p:addr) (x y:N) (t:trace) := cycle_count t."


def test_render_contains_theorem_and_invariant():
    src = render(SPEC, INV, "timing_invs")
    assert "Theorem addloop_timing" in src
    # The functor template states the goal over the spec's lifted_program / exit_point, with the
    # model-supplied invariant the only variable part of the (pinned) postcondition.
    assert "satisfies_all addloop (timing_invs x y) addloop_exit" in src
    assert "Require Import Picinae_riscv." in src
    assert "(x : N) (y : N)" in src
    # No register bindings on the params -> addloop's R_T0/R_T1 calling-convention fallback.
    assert "(T0: s R_T0 = x)" in src
    assert "(T1: s R_T1 = y)" in src
    # Functor scoping (the fix for `startof not found`) is preserved.
    assert "Module addloop_timing_Proof (cpu : RVCPUTimingBehavior)." in src
    # Default render leaves the proof admitted and closes with the concrete CPU instantiation.
    assert "Admitted." in src
    assert src.strip().endswith("Module addloop_timing_Concrete := addloop_timing_Proof addloop_timing_CPU.")


# A second, distinct program: different requires, program/automation modules, and an a0/a1
# calling convention supplied per-param. Nothing addloop-specific should leak in.
CT_SPEC = TargetSpec(
    name="ctselect",
    requires=["NEORV32", "RISCVTiming", "riscv_ctselect_timing_proof"],
    lifted_program="ctsel_prog",
    entry_addr=0x0,
    exit_point="ctsel_exits",
    theorem_name="ctselect_timing",
    params=[("sel", "N", "R_A0"), ("secret", "N", "R_A1")],
    program_module="Program_ctselect",
    auto_module="ctselectAuto",
)

CT_INV = "Definition ctselect_timing_invs (sel secret : N) (t:trace) := True."


def test_render_second_program():
    src = render(CT_SPEC, CT_INV, "ctselect_timing_invs", proof_body="hammer. Qed.")
    # Requires are driven from the spec.
    assert "Require Import riscv_ctselect_timing_proof." in src
    # Binders.
    assert "(sel : N) (secret : N)" in src
    # The program/automation modules come from the spec, and addloop's do not leak in.
    assert "Import Inner.Program_ctselect." in src
    assert "Import Inner.ctselectAuto." in src
    assert "Program_addloop" not in src
    assert "addloopAuto" not in src
    assert "riscv_addloop_timing_proof" not in src
    # Entry hypotheses come from each param's bound register (R_A0 -> A0, etc.).
    assert "(A0: s R_A0 = sel)" in src
    assert "(A1: s R_A1 = secret)" in src
    # The pinned postcondition uses the spec's lifted_prog / exits.
    assert "satisfies_all ctsel_prog (ctselect_timing_invs sel secret) ctsel_exits" in src
    # Functor wrapper preserved.
    assert "Module ctselect_timing_Proof (cpu : RVCPUTimingBehavior)." in src
    # Proof is completed (Qed). The functor's `End ..._Proof.` necessarily follows, so the file
    # cannot literally *end* in Qed without dropping the wrapper; assert the proof closes.
    assert "Qed." in src


def test_malformed_param_raises_naming_the_param():
    bad = TargetSpec(
        name="bad", requires=["X"], lifted_program="p", entry_addr=0, exit_point="e",
        theorem_name="bad_thm", params=[("x", "N", "R_A0", "oops")],
    )
    with pytest.raises(ValueError, match="malformed param"):
        render(bad, "Definition i := True.", "i")
