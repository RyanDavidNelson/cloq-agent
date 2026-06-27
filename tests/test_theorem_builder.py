"""Theorem rendering produces a well-formed, parametrized timing theorem."""
import sys
from pathlib import Path

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
    assert "satisfies_all addloop (timing_invs x y)" in src
    assert "Require Import Picinae_riscv." in src
    assert "(x : N) (y : N)" in src
    assert src.strip().endswith("Qed.")
