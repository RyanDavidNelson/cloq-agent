"""GAP 2, bottom-test gate. The top-vs-bottom discriminator, plus the SIX hand-traces for se_find_eq
(gcc -O2 rotated do-while) reconciling literal block-by-block cost against the derived bottom-test
closed form — not-found at len 0/1/2, found at idx 0/1/2. This is the gate BEFORE the closer: the
peel (len=0 guard) and the latch-fused body are the two new failure surfaces, and they only show at
the boundaries, so all six must reconcile term-for-term.

The decomposition (hand-derived from the se_find_eq CFG, costs in the NEORV32 t* model):
  PRO            = mv,mv,beqz(ft),li,j           (prologue, len>=1)
  BODY_CONT      = lw,addi,bne(taken) ; addi,beq(ft)   (a continuing iteration: body + latch-fallthrough)
  BODY_EXIT      = lw,addi,bne(taken) ; addi,beq(TAKEN) (last not-found iteration: latch bound taken)
  FOUND_PARTIAL  = lw,addi,bne(ft)               (found exits mid-body, BEFORE the latch)
  SHUT_NF / SHUT_F differ per arm (not-found rets directly; found does `mv a0,a4` first).
Two surfaces beyond top-test: per-arm shutdown, and the len=0 GUARD (a distinct path that skips the
prologue tail + body), which the general len>=1 term does not cover.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.lift.cfg import build_cfg, parse_objdump

_REPO = Path(__file__).resolve().parents[1]

_PRO = Counter(["taddi", "taddi", "tfbeq", "taddi", "tjal"])
_BODY_CONT = Counter(["tlw", "taddi", "ttbne", "taddi", "tfbeq"])
_BODY_EXIT = Counter(["tlw", "taddi", "ttbne", "taddi", "ttbeq"])
_FOUND_PARTIAL = Counter(["tlw", "taddi", "tfbne"])
_SHUT_NF = Counter(["tjalr"])
_SHUT_F = Counter(["taddi", "tjalr"])
_GUARD_LEN0 = Counter(["taddi", "taddi", "ttbeq", "tjalr"])   # mv,mv,beqz(TAKEN),ret


def _literal_notfound(n):
    if n == 0:
        return Counter(_GUARD_LEN0)
    c = Counter(_PRO)
    for _ in range(n - 1):
        c += _BODY_CONT
    return c + _BODY_EXIT + _SHUT_NF


def _literal_found(i):
    c = Counter(_PRO)
    for _ in range(i):
        c += _BODY_CONT
    return c + _FOUND_PARTIAL + _SHUT_F


def _derived_notfound(n):       # the bottom-test closed form (len>=1); len=0 is the guard case
    if n == 0:
        return Counter(_GUARD_LEN0)
    return Counter(_PRO) + Counter({k: v * (n - 1) for k, v in _BODY_CONT.items()}) + _BODY_EXIT + _SHUT_NF


def _derived_found(i):
    return Counter(_PRO) + Counter({k: v * i for k, v in _BODY_CONT.items()}) + _FOUND_PARTIAL + _SHUT_F


def test_discriminator_classifies_top_and_bottom_test():
    fia = build_cfg(parse_objdump((_REPO / "eval" / "targets" / "find_in_array.objdump").read_text()))
    se = build_cfg(parse_objdump((_REPO / "eval" / "heldout" / "se_find_eq.objdump").read_text()))
    assert fia.loop_is_bottom_test(fia.loop_headers[0]) is False     # vendored: top-test
    assert se.loop_is_bottom_test(se.loop_headers[0]) is True        # gcc -O2: bottom-test


def test_six_traces_reconcile_literal_vs_derived():
    for n in (0, 1, 2):
        assert _literal_notfound(n) == _derived_notfound(n), f"not-found len={n}"
    for i in (0, 1, 2):
        assert _literal_found(i) == _derived_found(i), f"found idx={i}"


def test_len0_is_a_distinct_guard_boundary():
    """The peel: the general len>=1 not-found term, evaluated at the boundary, is NOT the literal
    len=0 trace (the guard skips the prologue tail and the body). time_of must case on len=0."""
    general_at_0 = Counter(_PRO) + _BODY_EXIT + _SHUT_NF        # what the n>=1 form would give at n=0
    assert general_at_0 != _literal_notfound(0)
    assert _literal_notfound(0) == _GUARD_LEN0


def test_found_and_notfound_shutdowns_differ():
    """A second new surface vs top-test find_in_array (shared shutdown): se_find_eq's found arm rets
    via `mv a0,a4` (taddi) while not-found rets directly."""
    assert _SHUT_F != _SHUT_NF


# --- the CFG-DERIVED time_of (not the hand decomposition) reconciles all six -------------------

def _terms(s):
    return Counter(s.split(" + ")) if s else Counter()


def _bt():
    cfg = build_cfg(parse_objdump((_REPO / "eval" / "heldout" / "se_find_eq.objdump").read_text()))
    return cfg.bottom_test_timing(cfg.loop_headers[0])


def test_cfg_derived_pieces_match_the_hand_decomposition():
    b = _bt()
    assert b is not None
    assert _terms(b.pro) == _PRO
    assert _terms(b.body_cont) == _BODY_CONT
    assert _terms(b.body_exit) == _BODY_EXIT
    assert _terms(b.found_partial) == _FOUND_PARTIAL
    assert _terms(b.shut_nf) == _SHUT_NF
    assert _terms(b.shut_f) == _SHUT_F
    assert _terms(b.guard) == _GUARD_LEN0


def test_cfg_derived_time_of_reconciles_all_six_with_boundaries():
    """Evaluate the GENERATED closed form at the six points; the two boundary cases (found idx=0,
    not-found len=0) are the ones that exercise the peel and the divergent shutdown."""
    b = _bt()

    def gen_notfound(n):
        if n == 0:
            return _terms(b.guard)
        return (_terms(b.pro) + Counter({k: v * (n - 1) for k, v in _terms(b.body_cont).items()})
                + _terms(b.body_exit) + _terms(b.shut_nf))

    def gen_found(i):
        return (_terms(b.pro) + Counter({k: v * i for k, v in _terms(b.body_cont).items()})
                + _terms(b.found_partial) + _terms(b.shut_f))

    for n in (0, 1, 2):
        assert gen_notfound(n) == _literal_notfound(n), f"not-found len={n}"
    for i in (0, 1, 2):
        assert gen_found(i) == _literal_found(i), f"found idx={i}"
    # the two boundary re-checks, called out explicitly
    assert gen_found(0) == _literal_found(0)        # the peeled first body, found-at-0
    assert gen_notfound(0) == _literal_notfound(0)  # the len=0 guard


def test_rendered_time_of_is_a_two_level_match():
    from cloq_agent.lift.search_template import time_of_bottom_test

    src = time_of_bottom_test("time_of_se_find_eq", "len", _bt())
    assert "match found_idx with" in src
    assert "Some i => " in src and "i * (" in src              # found counts iterations
    assert "match len with" in src and "| 0 => " in src        # the guard boundary, in time_of
    assert "(len - 1) * (" in src                              # not-found len>=1 term
