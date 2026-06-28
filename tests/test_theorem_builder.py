"""Theorem rendering produces a well-formed, parametrized timing theorem.

Two fixtures: the addloop smoke target (defaults) and a second, distinct program (CT-style,
different requires / program module / register convention) to prove the template is no longer
hardcoded to addloop.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.proof.theorem_builder import TargetSpec, render, write

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
    # Default render (proof_body=None) leaves the proof OPEN with the deterministic prelude:
    # the per-proof `step` binding + Picinae setup + destruct_inv, and NO closer.
    assert "Proof." in src
    assert "Local Ltac step := tstep r5_step." in src
    assert "apply prove_invs." in src
    assert "simpl. rewrite ENTRY. unfold entry_addr. now step." in src
    assert "eapply startof_prefix in ENTRY; try eassumption." in src
    assert "eapply preservation_exec_prog in MDL; try eassumption; [idtac|apply lift_riscv_welltyped]." in src
    assert "clear - ENTRY PRE MDL. rename t1 into t. rename s1 into s'." in src
    assert "destruct_inv 32 PRE." in src
    # No malformed/leaked closer in an open proof: no Admitted., no Qed., no functor End /
    # concrete instantiation (an open proof cannot be followed by `End ..._Proof.`).
    assert "Admitted." not in src
    assert "Qed." not in src
    assert "End addloop_timing_Proof." not in src
    assert "addloop_timing_Concrete" not in src
    # The open file ends on the prelude's last tactic.
    assert src.strip().endswith("destruct_inv 32 PRE.")


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
    # A supplied (closed) proof_body emits the script, then the functor `End` + concrete-CPU
    # instantiation. The proof closes (Qed) and the file ends on the concrete instantiation.
    assert "Qed." in src
    assert "End ctselect_timing_Proof." in src
    assert src.strip().endswith(
        "Module ctselect_timing_Concrete := ctselect_timing_Proof ctselect_timing_CPU."
    )
    # The deterministic prelude is NOT injected when the caller supplies its own script.
    assert "Local Ltac step := tstep r5_step." not in src


def test_addr_width_parametrizes_destruct_inv():
    """addr_width drives `destruct_inv {width} PRE` (default 32; e.g. 64 for RV64)."""
    spec = TargetSpec(
        name="w64", requires=["X"], lifted_program="p", entry_addr=0,
        exit_point="e", theorem_name="w64_thm", params=[("x", "N")], addr_width=64,
    )
    src = render(spec, "Definition i (x:N) (t:trace) := True.", "i")
    assert "destruct_inv 64 PRE." in src
    assert "destruct_inv 32 PRE." not in src


def test_open_proof_has_no_admitted_or_qed():
    """The default open render must never leak Admitted./Qed. (the old malformed template
    rendered `Admitted.` then a closer, an invalid proof)."""
    src = render(SPEC, INV, "timing_invs")
    assert "Admitted." not in src
    assert "Qed." not in src


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


def test_build_spec_filename_uses_target_key_not_lifted_program(tmp_path):
    """The generated filename comes from the target KEY (addloop -> Addloop_gen.v), not from
    `lifted_program` (which produced the `Lifted_prog_gen.v` bug)."""
    from eval.targets import build_spec

    t = {
        "requires": ["NEORV32", "RISCVTiming", "riscv_addloop_timing_proof"],
        "lifted_program": "lifted_prog",
        "entry_addr": "0x8",
        "exit_point": "exits",
        "theorem_name": "addloop_timing_gen",
        "params": [["x", "N"], ["y", "N"]],
    }
    spec, *_ = build_spec(t, Path("/nonexistent"), name="addloop")
    assert spec.name == "addloop"            # the target key, NOT "lifted_prog"
    assert spec.lifted_program == "lifted_prog"

    out = write(spec, render(spec, INV, "timing_invs"), tmp_path)
    assert out.name == "Addloop_gen.v"


def test_malformed_param_raises_naming_the_param():
    bad = TargetSpec(
        name="bad", requires=["X"], lifted_program="p", entry_addr=0, exit_point="e",
        theorem_name="bad_thm", params=[("x", "N", "R_A0", "oops")],
    )
    with pytest.raises(ValueError, match="malformed param"):
        render(bad, "Definition i := True.", "i")
