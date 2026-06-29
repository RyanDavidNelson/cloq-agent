"""Phase 2 — array-search decidability template. Unit tests (server-free) for the shape recovery
and emitter; a server-gated test that the emitted decidability block actually type-checks (both the
`i << 2` and `4 * i` address forms), proving `key_in_array_dec` is genericizable, not bespoke."""
from __future__ import annotations

import re
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.config import load_config
from cloq_agent.lift.cfg import build_cfg, parse_objdump
from cloq_agent.lift.search_template import (
    ArrayShape,
    case_split_tactic,
    decidability_block,
    timing_postcondition_block,
)

_REPO = Path(__file__).resolve().parents[1]
_CFG = load_config()
_TARGETS = str(_REPO / "eval" / "targets.yaml")
_FIA_OBJDUMP = _REPO / "eval" / "targets" / "find_in_array.objdump"


# --- shape + emitter (no server) ---------------------------------------------

def test_addr_expr_shift_vs_mul():
    assert ArrayShape("R_A0", "R_A5", 4, shift_form=True).addr_expr("i") == "i << 2"
    assert ArrayShape("R_A0", "R_A5", 4, shift_form=False).addr_expr("i") == "4 * i"
    assert ArrayShape("R_A0", "R_A5", 2, shift_form=True).addr_expr("len") == "len << 1"


def test_recover_find_in_array_shape_from_objdump():
    cfg = build_cfg(parse_objdump(_FIA_OBJDUMP.read_text()))
    header = cfg.loop_headers[0]
    shape = cfg.array_search_shape(header)
    assert shape is not None
    assert shape.base_reg == "R_A0"      # add t1, a0, t0  -> base is a0
    assert shape.index_reg == "R_A5"     # slli t0, a5, 0x2 / addi a5,a5,1
    assert shape.elem_bytes == 4         # lw
    assert shape.shift_form is True      # via slli


def test_decidability_block_specialises_address_and_compiles_shape():
    shape = ArrayShape("R_A0", "R_A5", 4, shift_form=True)
    block = decidability_block(shape)
    assert "exists i, i < len /\\ mem Ⓓ[arr + (i << 2)] = key" in block
    assert "Fixpoint key_in_array_dec" in block
    assert "N.eq_dec (mem Ⓓ[arr + (len << 2)]) key" in block
    # the `4 * i` form differs ONLY in the address expression (the template claim)
    opt = decidability_block(ArrayShape("R_A0", "R_A5", 4, shift_form=False))
    assert "arr + (4 * i)" in opt
    assert block.replace("i << 2", "4 * i").replace("len << 2", "4 * len") == opt


def test_timing_postcondition_is_found_notfound_disjunction():
    block = timing_postcondition_block(ArrayShape("R_A0", "R_A5", 4, True), "time_of_find")
    assert "\\/" in block                                   # found OR not-found
    assert "forall j, j < i ->" in block                    # i is the FIRST match
    assert "time_of_find len (Some i) t" in block
    assert "time_of_find len None t" in block


def test_case_split_tactic_shape():
    assert case_split_tactic() == \
        "destruct (key_in_array_dec (s' V_MEM32) arr key len) as [IN | NOT_IN]."


# --- emitted block type-checks (real pet-server) -----------------------------

def _pet_server_up() -> bool:
    try:
        with socket.create_connection((_CFG.petanque.host, _CFG.petanque.port), timeout=1.0):
            return True
    except OSError:
        return False


def test_build_spec_emits_template_and_renames_reused_invariant_and_proof():
    """find_in_array_tmpl: build_spec emits the cloq_-namespaced decidability + disjunction into
    spec.search_defs, and rewrites the reused gold invariant/proof to those names — so the vendored
    `timing_postcondition` / `key_in_array_dec` are no longer what the proof drives (server-free)."""
    from eval.targets import build_spec, load_targets

    t = load_targets(_TARGETS)["find_in_array_tmpl"]
    spec, _d, _s, gold_inv, gold_proof, _sk = build_spec(t, _REPO, "find_in_array_tmpl")
    assert spec.search_defs is not None
    assert "Fixpoint cloq_key_in_array_dec" in spec.search_defs
    assert "Definition cloq_timing_postcondition" in spec.search_defs
    assert "i << 2" in spec.search_defs                          # specialised to the shape
    # the reused invariant now points at the EMITTED disjunction, not the vendored one
    assert gold_inv is not None and "cloq_timing_postcondition" in gold_inv
    assert re.search(r"(?<!cloq_)timing_postcondition", gold_inv) is None
    # the reused proof drives the EMITTED decidability / trichotomy
    assert any("cloq_key_in_array_dec" in ln for ln in gold_proof)
    assert any("cloq_lt_impl_lt_or_eq" in ln for ln in gold_proof)


@pytest.mark.skipif(not _pet_server_up(),
                    reason=f"no pet-server at {_CFG.petanque.host}:{_CFG.petanque.port}")
def test_find_in_array_tmpl_closes_with_emitted_decidability():
    """Phase-2 end-to-end: the find_in_array proof reaches Qed driving the EMITTED (templated)
    decidability + case-split, with the vendored decidability namespaced away. The scaffold the
    prover loaded genuinely contains our emitted defs."""
    from cloq_agent.proof.petanque_driver import PetanqueDriver

    from eval.replay import replay_gold_arms

    with PetanqueDriver(_CFG.petanque, default_timeout_s=_CFG.agent.tactic_timeout_s) as d:
        rep = replay_gold_arms(d, "find_in_array_tmpl", targets_file=_TARGETS, repo_root=_REPO)
    assert rep.started, f"theorem did not elaborate: {rep.start_error}"
    assert rep.closed, f"emitted-template find_in_array did not reach Qed (arms: {rep.arms})"
    scaffold = Path(rep.scaffold_path).read_text()
    assert "Fixpoint cloq_key_in_array_dec" in scaffold          # emitted, not vendored
    assert "cloq_timing_postcondition" in scaffold
    assert any("cloq_key_in_array_dec" in a.tactic for a in rep.arms)   # the case-split used it


@pytest.mark.skipif(not _pet_server_up(),
                    reason=f"no pet-server at {_CFG.petanque.host}:{_CFG.petanque.port}")
@pytest.mark.parametrize("shift_form", [True, False])
def test_emitted_decidability_block_typechecks(shift_form):
    """Load `key_in_array` + `key_in_array_dec` (for the recovered element width) through Rocq; a
    `start` on the probe theorem compiles everything before it. Both address forms must elaborate."""
    from cloq_agent.proof.petanque_driver import PetanqueDriver

    shape = ArrayShape("R_A0", "R_A5", 4, shift_form=shift_form)
    src = ("Require Import NArith.\nRequire Import Picinae_riscv.\nImport RISCVNotations.\n"
           "Require Import Lia.\n\n" + decidability_block(shape) +
           "\n\nTheorem probe_thm : True. Proof. exact I. Qed.\n")
    workspace = Path(_CFG.petanque.workspace)
    v = workspace / "targets" / "SearchTemplateProbe.v"
    v.write_text(src)
    try:
        with PetanqueDriver(_CFG.petanque, default_timeout_s=60.0) as d:
            start = d.start(f"{_CFG.petanque.workspace}/targets/SearchTemplateProbe.v", "probe_thm")
        assert start.ok, f"emitted decidability block did not compile: {start.error}"
    finally:
        v.unlink(missing_ok=True)
