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


def test_entry_hyps_emitted_after_register_ties():
    """Extra precondition hypotheses (alignment / in-bounds) that are not single register ties
    render verbatim after the register hypotheses — needed for pointer targets like ct_swap."""
    spec = TargetSpec(
        name="ct_swap",
        requires=["NEORV32", "RISCVTiming", "ct_swap_proof"],
        lifted_program="lifted_prog",
        entry_addr=0x1e4,
        exit_point="exits",
        theorem_name="ct_swap_timing_gen",
        params=[("len", "N", "R_A3"), ("base_addr_b", "N", "R_A2")],
        program_module="Program_ct_swap",
        auto_module="ct_swapAuto",
        entry_hyps=[("PTR_ALIGN", "exists k', base_addr_b = 4 * k'"),
                    ("LEN_VALID", "4 * len < 2^32")],
    )
    src = render(spec, "Definition ct_swap_timing_invs (len base_addr_b : N) (t:trace) := True.",
                 "ct_swap_timing_invs")
    # Register ties from params, then the extra hypotheses verbatim.
    assert "(A3: s R_A3 = len)" in src
    assert "(A2: s R_A2 = base_addr_b)" in src
    assert "(PTR_ALIGN: exists k', base_addr_b = 4 * k')" in src
    assert "(LEN_VALID: 4 * len < 2^32)" in src
    # Register ties precede the extra hypotheses (the entry-invariant arm is order-insensitive,
    # but keeping ties first matches the vendored convention).
    assert src.index("(A2: s R_A2 = base_addr_b)") < src.index("(PTR_ALIGN:")


def test_extra_binders_add_binder_and_reg_hyp_but_not_inv_arg():
    """An ABI register the timing invariant ignores (e.g. vListInsertEnd's a1) becomes a
    universally-quantified binder + register hypothesis, but is NOT passed to the invariant."""
    spec = TargetSpec(
        name="vListInsertEnd",
        requires=["NEORV32", "RISCVTiming", "vListInsertEnd"],
        lifted_program="lifted_prog",
        entry_addr=0x800023c4,
        exit_point="exits",
        theorem_name="vListInsertEnd_timing_gen",
        params=[("base_mem", "memory"), ("a0", "N", "R_A0")],
        program_module="Program_vListInsertEnd",
        auto_module="vListInsertEndAuto",
        extra_binders=[("a1", "N", "R_A1")],
    )
    src = render(spec, "Definition vListInsertEnd_timing_invs (base_mem : memory) (a0 : N) (t:trace) := True.",
                 "vListInsertEnd_timing_invs")
    # a1 is a forall binder and gets a register hypothesis...
    assert "(a1 : N)" in src
    assert "(A1: s R_A1 = a1)" in src
    assert "(A0: s R_A0 = a0)" in src
    # ...but it is NOT an invariant argument (inv args stay base_mem a0).
    assert "(vListInsertEnd_timing_invs base_mem a0)" in src
    assert "vListInsertEnd_timing_invs base_mem a0 a1" not in src


def test_inv_args_override_prepends_store_arg():
    """A vendored invariant that takes the universally-quantified store `s` as a leading arg is
    driven via inv_args, while binders/inv params exclude `s` (find_in_array's shape)."""
    spec = TargetSpec(
        name="find_in_array",
        requires=["NEORV32", "array", "RISCVTiming", "find_in_array_proof"],
        lifted_program="lifted_prog",
        entry_addr=0x1e4,
        exit_point="exits",
        theorem_name="find_in_array_timing_gen",
        params=[("base_mem", "memory"), ("arr", "N", "R_A0"), ("len", "N", "R_A2")],
        program_module="Program_find_in_array",
        auto_module="find_in_arrayAuto",
        inv_args=["s", "base_mem", "arr", "len"],
    )
    src = render(spec, "Definition find_in_array_timing_invs (s : store) (base_mem : memory) (arr : N) (len : N) (t:trace) := True.",
                 "find_in_array_timing_invs")
    # The invariant is applied to the store `s` first, then the params.
    assert "(find_in_array_timing_invs s base_mem arr len)" in src
    # `s` is the template's forall-bound store, not an extra binder.
    assert "(s : " not in src.split("Theorem")[1]  # no `(s : ...)` binder in the theorem head


def test_no_entry_hyps_by_default():
    """A spec without entry_hyps emits only the register-tie hypotheses (addloop back-compat)."""
    src = render(SPEC, INV, "timing_invs")
    assert "PTR_ALIGN" not in src and "LEN_VALID" not in src


def test_malformed_param_raises_naming_the_param():
    bad = TargetSpec(
        name="bad", requires=["X"], lifted_program="p", entry_addr=0, exit_point="e",
        theorem_name="bad_thm", params=[("x", "N", "R_A0", "oops")],
    )
    with pytest.raises(ValueError, match="malformed param"):
        render(bad, "Definition i := True.", "i")
