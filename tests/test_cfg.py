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
