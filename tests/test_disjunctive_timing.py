"""GAP 2 — disjunctive search timing, validated against the vendored `time_of_find_in_array` as the
oracle (term-for-term). Server-free: pure CFG derivation over the committed objdump.

The oracle match proves the MECHANISM: a search loop's found/not-found forms share setup + n*body +
shutdown and fork only on the partial last iteration, attributed by which exit edge (bound vs match)
each arm takes. Applying it to a held-out function additionally needs rotated-loop normalization
(gcc -O2's `j` into the loop body), tracked separately."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.lift.cfg import build_cfg, parse_objdump
from cloq_agent.lift.search_template import time_of_definition

_REPO = Path(__file__).resolve().parents[1]


def _fia_timing():
    cfg = build_cfg(parse_objdump((_REPO / "eval" / "targets" / "find_in_array.objdump").read_text()))
    return cfg.search_loop_timing(cfg.loop_headers[0])


def test_reproduces_vendored_time_of_find_in_array_term_for_term():
    """The four-plus-one parts of the vendored time_of_find_in_array, derived from the CFG alone."""
    st = _fia_timing()
    assert st is not None
    assert st.setup == "taddi"
    assert st.body == "tfbgeu + tslli 2 + tadd + tlw + tfbeq + taddi + tjal"
    assert st.found_partial == "tfbgeu + tslli 2 + tadd + tlw + ttbeq"   # match exits (taken)
    assert st.notfound_partial == "ttbgeu"                               # bound exits (taken)
    assert st.shutdown == "taddi + tjalr"


def test_rendered_definition_has_the_disjunctive_shape():
    src = time_of_definition("time_of_find_in_array", "len", _fia_timing())
    assert "(match found_idx with None => len | Some i => i end) *" in src
    assert "None => ttbgeu | Some _ => tfbgeu + tslli 2 + tadd + tlw + ttbeq end" in src
    assert src.rstrip().endswith("taddi + tjalr.")


def test_pure_walk_is_not_a_disjunctive_search():
    """ap_ptr_walk has one exit (the pointer bound) and no data-dependent match -> no disjunction."""
    cfg = build_cfg(parse_objdump((_REPO / "eval" / "heldout" / "ap_ptr_walk.objdump").read_text()))
    assert cfg.search_loop_timing(cfg.loop_headers[0]) is None
