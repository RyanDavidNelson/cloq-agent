"""Premise-satisfiability gate (the no-FPGA integrity check). Unit tests (server-free) for the
obligation builder; server-gated tests that a SATISFIABLE premise set passes and a contradictory one
fires the gate — catching a vacuously-true theorem at generation time."""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.config import load_config
from cloq_agent.proof.premise_check import (
    input_premises,
    premise_obligation,
)

from eval.targets import build_spec, load_targets

_REPO = Path(__file__).resolve().parents[1]
_CFG = load_config()
_TARGETS = str(_REPO / "eval" / "targets.yaml")


def _spec(name):
    spec, *_ = build_spec(load_targets(_TARGETS)[name], _REPO, name=name)
    return spec


# --- unit: obligation builder (no server) ------------------------------------

def test_input_premises_excludes_state_ties():
    # find_in_array carries MEM (`s V_MEM32 = base_mem`, a state tie) and LEN_VALID (pure).
    prems = dict(input_premises(_spec("find_in_array")))
    assert "LEN_VALID" in prems
    assert "MEM" not in prems                       # state ties are not vacuity-risk premises


def test_obligation_quantifies_premises_for_ct_swap():
    src = premise_obligation(_spec("ct_swap"))
    assert src is not None
    assert "Lemma premise_sat : exists" in src
    assert "4 * len < 2^32" in src                  # LEN_VALID
    assert "base_addr_b = 4 * k'" in src            # PTR_ALIGN
    assert "(len : N)" in src and "(base_addr_b : N)" in src  # only the mentioned binders, typed


def test_no_premises_no_obligation():
    # addloop has no entry_hyps -> nothing to check.
    assert premise_obligation(_spec("addloop")) is None


# --- integration: the gate (real pet-server) ---------------------------------

def _pet_server_up() -> bool:
    try:
        with socket.create_connection((_CFG.petanque.host, _CFG.petanque.port), timeout=1.0):
            return True
    except OSError:
        return False


pytestmark_server = pytest.mark.skipif(
    not _pet_server_up(), reason=f"no pet-server at {_CFG.petanque.host}:{_CFG.petanque.port}")


@pytestmark_server
@pytest.mark.parametrize("name", ["ct_swap", "find_in_array"])
def test_real_premises_are_satisfiable(name):
    from cloq_agent.proof.petanque_driver import PetanqueDriver
    from cloq_agent.proof.premise_check import check_premises_satisfiable

    with PetanqueDriver(_CFG.petanque, default_timeout_s=_CFG.agent.tactic_timeout_s) as d:
        ok, why = check_premises_satisfiable(d, _spec(name), Path(_CFG.petanque.workspace))
    assert ok, f"{name} premises should be satisfiable: {why}"


@pytestmark_server
def test_contradictory_premise_fires_the_gate():
    """A vacuous theorem (impossible premise) must be REJECTED at generation time, not proved."""
    from cloq_agent.proof.petanque_driver import PetanqueDriver
    from cloq_agent.proof.premise_check import check_premises_satisfiable

    spec = _spec("ct_swap")
    spec.entry_hyps = list(spec.entry_hyps) + [("BOGUS", "len = 1 /\\ len = 2")]
    with PetanqueDriver(_CFG.petanque, default_timeout_s=_CFG.agent.tactic_timeout_s) as d:
        ok, why = check_premises_satisfiable(d, spec, Path(_CFG.petanque.workspace))
    assert ok is False
    assert why is not None and "satisf" in why.lower()
