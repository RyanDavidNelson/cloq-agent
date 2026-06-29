"""Loop membership = the header's SCC (rotation-robust). This guards the FOUNDATION: `_natural_loop`
feeds `loop_timing` for every loop class, so the SCC switch must be a byte-identical no-op on every
shipped loop and must drop the preheader that gcc's rotated loop otherwise pollutes the body with."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.lift.cfg import build_cfg, parse_objdump

_REPO = Path(__file__).resolve().parents[1]


def _cfg(rel):
    return build_cfg(parse_objdump((_REPO / rel).read_text()))


# The bodies that ship today — lock them so the SCC change (and any future change) is a no-op here.
_SHIPPED_BODIES = {
    "eval/targets/addloop.objdump": "tfbeq + taddi + tsub + ttbeq",
    "eval/targets/ct_swap.objdump":
        "ttbne + tlw + tlw + taddi + taddi + txor + tand + txor + tsw + tlw + txor + tsw + tjal",
    "eval/targets/find_in_array.objdump":
        "tfbgeu + tslli 2 + tadd + tlw + tfbeq + taddi + tjal",
}


def test_scc_is_byte_identical_no_op_on_shipped_loops():
    for rel, body in _SHIPPED_BODIES.items():
        cfg = _cfg(rel)
        assert cfg.loop_timing(cfg.loop_headers[0])[1] == body, rel


def test_rotated_loop_excludes_preheader_se_find_eq():
    """se_find_eq is a gcc -O2 rotated (`j` into body) loop; the SCC must be just the latch+body
    {0x14,0x1c}, not the preheader {0x0,0xc} the textbook natural-loop walk would pull in."""
    cfg = _cfg("eval/heldout/se_find_eq.objdump")
    h = cfg.loop_headers[0]
    assert cfg._natural_loop(h) == {0x14, 0x1c}
    assert cfg.loop_timing(h)[1] == "taddi + tfbeq + tlw + taddi + ttbne"   # 5 terms, no preheader


def test_natural_loop_equals_scc_intersection():
    """The definition itself: loop membership is forward(header) ∩ backward(header)."""
    for rel in [*_SHIPPED_BODIES, "eval/heldout/se_find_eq.objdump", "eval/heldout/ap_ptr_walk.objdump"]:
        cfg = _cfg(rel)
        h = cfg.loop_headers[0]
        scc = cfg._reachable(h, forward=True) & cfg._reachable(h, forward=False)
        assert cfg._natural_loop(h) == (scc | {h}), rel
