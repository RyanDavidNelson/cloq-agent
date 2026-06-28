"""CFG recovery + loop detection on the addloop listing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.lift.cfg import build_cfg, parse_objdump

ADDLOOP = """
00000000 <add>:
   0:\t00028463 \tbeqz\tt0,10 <end>
   4:\t00130313 \taddi\tt1,t1,1
   8:\tfff28293 \taddi\tt0,t0,-1
   c:\tff5ff06f \tj\t0 <add>
00000010 <end>:
  10:\t00008067 \tret
"""


def test_parses_all_instructions():
    insns = parse_objdump(ADDLOOP)
    assert [i.mnemonic for i in insns] == ["beqz", "addi", "addi", "j", "ret"]


def test_detects_the_loop():
    cfg = build_cfg(parse_objdump(ADDLOOP))
    # the back-edge from the `j 0` block to address 0 is the loop header
    assert 0 in cfg.loop_headers
    assert cfg.entry == 0


def test_describe_is_nonempty():
    cfg = build_cfg(parse_objdump(ADDLOOP))
    desc = cfg.describe()
    assert "loop headers" in desc and "0x0" in desc


# The real lifted addloop listing (entry 0x8, exit 0x20) shipped as an eval fixture.
_ADDLOOP_OBJDUMP = Path(__file__).resolve().parents[1] / "eval" / "targets" / "addloop.objdump"


def test_invariant_points_for_addloop_fixture():
    cfg = build_cfg(parse_objdump(_ADDLOOP_OBJDUMP.read_text()))
    points = cfg.invariant_points()

    # Assert against what the parser actually recovered, not a hardcoded list: the points are
    # exactly entry + loop headers + exits, de-duplicated and ordered.
    expected = sorted({cfg.entry, *cfg.loop_headers, *cfg.exit_points()})
    assert points == expected

    # And those parser-derived addresses are the documented addloop ones: entry 0x8, a single
    # loop header, exit 0x20.
    assert cfg.entry == 0x8
    assert cfg.exit_points() == [0x20]
    assert len(cfg.loop_headers) == 1
    (loop_header,) = cfg.loop_headers
    assert points == [0x8, loop_header, 0x20]
    assert 0x8 < loop_header < 0x20  # the header sits inside the body, between entry and exit


# A function with a forward branch whose two arms rejoin before a later `ret` — exercises
# join-point detection and the ret-as-exit fix (the straight-line exit bug + uxListRemove shape).
_BRANCHY = """
00000000 <f>:
   0:\t00b50463 \tbne\ta0,a1,c <f+0xc>
   4:\t00150513 \taddi\ta0,a0,1
   8:\t00c0006f \tj\t10 <f+0x10>
   c:\t00160613 \taddi\ta2,a2,1
  10:\t00170693 \taddi\ta3,a3,1
  14:\t00008067 \tret
"""


def test_join_point_and_ret_exit():
    cfg = build_cfg(parse_objdump(_BRANCHY))
    assert cfg.entry == 0x0
    assert cfg.loop_headers == []           # forward branch only, no back-edge
    assert cfg.join_points() == [0x10]      # the two arms rejoin at 0x10
    assert cfg.exit_points() == [0x14]      # the `ret` address, not the entry/block-start
    assert cfg.invariant_points() == [0x0, 0x10, 0x14]


def test_straightline_exit_is_the_ret_address():
    """A straight-line function (no branches) must report its `ret` as the exit, distinct from
    the entry — the bug that previously made invariant_points = [entry] only."""
    listing = """
00000000 <g>:
   0:\t00100513 \taddi\ta0,zero,1
   4:\t00052023 \tsw\ta0,0(a0)
   8:\t00008067 \tret
"""
    cfg = build_cfg(parse_objdump(listing))
    assert cfg.entry == 0x0
    assert cfg.exit_points() == [0x8]
    assert cfg.invariant_points() == [0x0, 0x8]


class _Spec:
    """Minimal spec stand-in: skeleton synthesis needs `params` + a pinned `postcondition`."""
    name = "addloop"
    params = [("x", "N"), ("y", "N")]
    postcondition = "cycle_count_of_trace t' = x * t_body"


def test_skeleton_pins_addresses_and_postcondition():
    cfg = build_cfg(parse_objdump(_ADDLOOP_OBJDUMP.read_text()))
    plan = cfg.skeleton_plan(_Spec())

    # Holes are entry + loop headers (the model's job); exits stay pinned, never a hole.
    assert plan.exit_addrs == cfg.exit_points()
    assert set(plan.hole_addrs).isdisjoint(plan.exit_addrs)
    assert set(plan.hole_addrs) | set(plan.exit_addrs) == set(cfg.invariant_points())

    # The skeleton shown to the model carries one greppable hole marker per hole address and the
    # pinned postcondition (verbatim) on the exit arm.
    for a in plan.hole_addrs:
        assert f"HOLE:0x{a:x}" in plan.prompt_text
    assert _Spec.postcondition in plan.prompt_text

    # fill() re-pins by construction: even if the model omits everything, addresses + match
    # scaffold + postcondition survive, and missing holes become an obviously-false sentinel.
    filled = plan.fill({})
    for a in cfg.invariant_points():
        assert f"| 0x{a:x} =>" in filled
    assert _Spec.postcondition in filled
    assert "UNFILLED HOLE" in filled
