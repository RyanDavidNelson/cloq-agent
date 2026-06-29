"""GAP 1 — unified running-pointer / indexed shape recovery, with the premise gate as the recovery
oracle. Held to the two held-out shapes (indexed word search se_find_eq, running-pointer word walk
ap_ptr_walk); byte stride and the disjunctive exit are GAP 2's problem.

Shape recovery is server-free (CFG analysis over committed objdump fixtures). The oracle test
(recover -> emit stride-derived premises -> the gate must pass) needs the pet-server.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.config import load_config
from cloq_agent.lift.cfg import build_cfg, parse_objdump
from cloq_agent.lift.search_template import shape_premises
from cloq_agent.proof.theorem_builder import TargetSpec

_REPO = Path(__file__).resolve().parents[1]
_CFG = load_config()
_HELDOUT = _REPO / "eval" / "heldout"


def _shape(objdump_name):
    cfg = build_cfg(parse_objdump((_HELDOUT / objdump_name).read_text()))
    return cfg.array_search_shape(cfg.loop_headers[0])


# --- recovery (server-free) --------------------------------------------------

def test_running_pointer_with_index_counter_se_find_eq():
    """se_find_eq: gcc -O2 keeps an `i<len` counter AND a running data pointer. Recovery must take
    the pointer's stride, trace base to the param, and resolve the bound through gcc's a0 reuse."""
    s = _shape("se_find_eq.objdump")
    assert s is not None
    assert s.moving_reg == "R_A5"          # the running data pointer feeding the load
    assert s.elem_bytes == 4               # stride = the pointer increment (word)
    assert s.base_reg == "R_A0"            # base = entry value of the pointer (arr), not a0-reused-as-len
    assert s.index_reg == "R_A4"           # the i<len counter
    assert s.bound_reg == "R_A2"           # len -- resolved THROUGH `mv a0,a2`, not the raw a0
    assert s.bound_kind == "index"
    assert s.shift_form is False


def test_running_pointer_range_bound_ap_ptr_walk():
    """ap_ptr_walk: no index counter; the pointer itself is the induction and the bound is `p<end`."""
    s = _shape("ap_ptr_walk.objdump")
    assert s is not None
    assert s.moving_reg == "R_A5" and s.index_reg == "R_A5"   # pointer IS the induction
    assert s.elem_bytes == 4
    assert s.base_reg == "R_A0"            # entry p
    assert s.bound_reg == "R_A1"           # end
    assert s.bound_kind == "pointer_range"


def test_indexed_form_unchanged_find_in_array():
    """Regression: the vendored indexed (slli;add;lw) form still recovers as the shift form."""
    cfg = build_cfg(parse_objdump((_REPO / "eval" / "targets" / "find_in_array.objdump").read_text()))
    s = cfg.array_search_shape(cfg.loop_headers[0])
    assert s.shift_form is True and s.moving_reg is None
    assert s.base_reg == "R_A0" and s.index_reg == "R_A5" and s.elem_bytes == 4


def test_inductions_are_uniform_over_counter_and_pointer():
    """Both the +1 counter and the +4 pointer are induction vars in the SAME representation."""
    cfg = build_cfg(parse_objdump((_HELDOUT / "se_find_eq.objdump").read_text()))
    inds = cfg._loop_inductions(cfg.loop_headers[0])
    assert ("R_A4", 1) in inds and ("R_A5", 4) in inds


# --- the premise gate as the recovery oracle (real pet-server) ---------------

def _pet_server_up() -> bool:
    try:
        with socket.create_connection((_CFG.petanque.host, _CFG.petanque.port), timeout=1.0):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _pet_server_up(),
                    reason=f"no pet-server at {_CFG.petanque.host}:{_CFG.petanque.port}")
@pytest.mark.parametrize("objdump,func", [("se_find_eq.objdump", "se_find_eq"),
                                          ("ap_ptr_walk.objdump", "ap_ptr_walk")])
def test_recovered_stride_premises_pass_the_gate(objdump, func):
    """Emit the well-formedness premises from the RECOVERED stride and require the premise gate to
    discharge them — the recovery oracle. A bogus stride would make the obligation degenerate/fail."""
    from cloq_agent.proof.petanque_driver import PetanqueDriver
    from cloq_agent.proof.premise_check import check_premises_satisfiable

    shape = _shape(objdump)
    binders, prems = shape_premises(shape)
    spec = TargetSpec(name=func, requires=[], lifted_program="p", entry_addr=0, exit_point="e",
                      theorem_name="t", params=[tuple(b) for b in binders], entry_hyps=prems)
    with PetanqueDriver(_CFG.petanque, default_timeout_s=_CFG.agent.tactic_timeout_s) as d:
        ok, why = check_premises_satisfiable(d, spec, Path(_CFG.petanque.workspace))
    assert ok, f"{func} recovered-stride premises should be satisfiable: {why}"
