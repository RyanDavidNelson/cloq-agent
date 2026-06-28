"""Skeleton-mode splice validation: holes are filled, the scaffold/postcondition are re-pinned,
and tampering is rejected. No LLM — we feed `_splice_skeleton` canned model output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.lift.cfg import build_cfg, parse_objdump
from cloq_agent.agent.invariant_synth import _splice_skeleton, _force_signature

_ADDLOOP_OBJDUMP = Path(__file__).resolve().parents[1] / "eval" / "targets" / "addloop.objdump"


class _Spec:
    name = "addloop"
    params = [("x", "N"), ("y", "N")]
    postcondition = "cycle_count_of_trace t' = tori + tandi + x * (tfbeq + taddi + tsub + ttbeq) + ttbeq"


def _plan():
    cfg = build_cfg(parse_objdump(_ADDLOOP_OBJDUMP.read_text()))
    return cfg.skeleton_plan(_Spec())


# The model copies the `(* PINNED ... *)` marker back into the exit-arm body verbatim. That is
# NOT an alteration of the proposition, so the splice must accept it (this was a false-reject bug).
_MODEL_WITH_ECHOED_COMMENT = """Definition timing_invs (x : N) (y : N) (t:trace) :=
match t with (Addr a, s) :: t' => match a with
| 0x8 => Some (s R_T0 = x /\\ s R_T1 = y /\\ cycle_count_of_trace t' = 0)
| 0x10 => Some (s R_T0 <= x /\\ cycle_count_of_trace t' = tori + tandi + (x - s R_T0) * (tfbeq + taddi + tsub + ttbeq))
| 0x20 => Some (cycle_count_of_trace t' = tori + tandi + x * (tfbeq + taddi + tsub + ttbeq) + ttbeq (* PINNED:0x20 postcondition from spec — do not change *))
| _ => None
end | _ => None end."""


def test_splice_accepts_echoed_pinned_comment_and_fills_holes():
    plan = _plan()
    out = _splice_skeleton(plan, _MODEL_WITH_ECHOED_COMMENT)
    assert out is not None
    # The model's hole bodies are spliced in...
    assert "s R_T0 = x" in out
    assert "(x - s R_T0)" in out
    # ...and the postcondition is the pinned one (no leftover marker comment in the body).
    assert _Spec.postcondition in out
    assert "do not change" not in out
    # No unfilled sentinels remain.
    assert "UNFILLED HOLE" not in out


def test_splice_rejects_a_genuinely_altered_postcondition():
    plan = _plan()
    tampered = _MODEL_WITH_ECHOED_COMMENT.replace(
        "cycle_count_of_trace t' = tori + tandi + x * (tfbeq + taddi + tsub + ttbeq) + ttbeq (* PINNED",
        "cycle_count_of_trace t' = 0 (* PINNED",
    )
    assert _splice_skeleton(plan, tampered) is None


def test_splice_rejects_a_changed_address():
    plan = _plan()
    tampered = _MODEL_WITH_ECHOED_COMMENT.replace("| 0x10 =>", "| 0x14 =>")
    assert _splice_skeleton(plan, tampered) is None


# --- freeform signature reconciliation (#2): drop a spurious leading binder, re-pin name + params.

_PARAMS = [("base_mem", "memory"), ("a0", "N")]


def test_force_signature_drops_spurious_address_binder():
    # The exact freeform failure mode: model adds a leading (p:addr) and an `: option Prop`, so the
    # rendered `(timing_invs base_mem a0)` is under-applied. Reconciliation fixes the header.
    bad = ("Definition timing_invs (p:addr) (base_mem : memory) (a0 : N) (t:trace) : option Prop :=\n"
           "match t with (Addr a, s) :: t' => match a with\n"
           "| 0x800023c4 => Some (cycle_count_of_trace t' = 0)\n"
           "| _ => None end | _ => None end.")
    out = _force_signature(bad, "timing_invs", _PARAMS)
    assert out.startswith("Definition timing_invs (base_mem : memory) (a0 : N) (t:trace) :=")
    assert "(p:addr)" not in out
    assert ": option Prop" not in out
    # Arm bodies are untouched.
    assert "0x800023c4 => Some (cycle_count_of_trace t' = 0)" in out


def test_force_signature_renames_to_canonical_and_keeps_body():
    bad = ("Definition foo (base_mem : memory) (a0 : N) (t:trace) :=\n"
           "match t with (Addr a, s) :: t' => match a with | 0x8 => Some (cycle_count_of_trace t' = 0) | _ => None end | _ => None end.")
    out = _force_signature(bad, "timing_invs", _PARAMS)
    assert out.startswith("Definition timing_invs (base_mem : memory) (a0 : N) (t:trace) :=")
    assert "0x8 => Some (cycle_count_of_trace t' = 0)" in out


def test_force_signature_noop_without_params():
    txt = "Definition timing_invs (p:addr) (t:trace) := whatever."
    assert _force_signature(txt, "timing_invs", None) == txt
