"""Unit tests for the C-intake lift stage: classification, scaffolding, and spec building.

These exercise the pure logic against canned disassembly (no toolchain needed); the gcc/objdump
and rocq legs are validated separately in CI's toolchain/rocq images.
"""
from __future__ import annotations

from cloq_agent.lift import intake
from cloq_agent.lift.cfg import build_cfg, parse_objdump
from cloq_agent.lift.intake import Ceiling

# A loop-free leaf function (two adds + return).
STRAIGHT = """\
00000000 <sum3>:
   0:\t00c585b3          \tadd\ta1,a1,a2
   4:\t00a58533          \tadd\ta0,a1,a0
   8:\t00008067          \tjalr\tzero,0(ra)
"""

# An array walk: a load inside a single-exit loop -> array/pointer ceiling.
ARRAY_LOOP = """\
00000000 <asum>:
   0:\t02058463          \tbeq\ta1,zero,1c <.L4>
   4:\t00259593          \tslli\ta1,a1,0x2
   8:\t00b506b3          \tadd\ta3,a0,a1
   c:\t0007a583          \tlw\ta1,0(a5)
  10:\t00478793          \taddi\ta5,a5,4
  14:\t00b50533          \tadd\ta0,a0,a1
  18:\tfed79ae3          \tbne\ta5,a3,c <asum+0xc>
  1c:\t00008067          \tjalr\tzero,0(ra)
"""

# A pure counter loop: no memory, single exit (addloop-shaped).
COUNTER_LOOP = """\
00000008 <add>:
   8:\t00106393          \tori\tt2,zero,1
   c:\t000e7e13          \tandi\tt3,t3,0
  10:\t01c28863          \tbeq\tt0,t3,20 <end>
  14:\t00130313          \taddi\tt1,t1,1
  18:\t407282b3          \tsub\tt0,t0,t2
  1c:\tffce0ae3          \tbeq\tt3,t3,10 <add>
00000020 <end>:
  20:\t00008067          \tjalr\tzero,0(ra)
"""


def _cfg(dump):
    return build_cfg(parse_objdump(dump))


def test_classify_straight_line():
    cfg = _cfg(STRAIGHT)
    assert cfg.loop_headers == []
    assert intake.classify(cfg) is Ceiling.STRAIGHT_LINE
    assert Ceiling.STRAIGHT_LINE.provable
    assert not Ceiling.STRAIGHT_LINE.is_ceiling


def test_classify_array_pointer_is_ceiling():
    cfg = _cfg(ARRAY_LOOP)
    cls = intake.classify(cfg)
    assert cls is Ceiling.ARRAY_POINTER
    assert not cls.provable
    assert cls.is_ceiling


def test_classify_counter_loop():
    cfg = _cfg(COUNTER_LOOP)
    cls = intake.classify(cfg)
    # A pure counter loop is provable in principle but not attempted by prove-c (needs a pinned
    # loop closed form), so it is not in the `provable` (straight-line) set and not a ceiling.
    assert cls is Ceiling.COUNTER_LOOP
    assert not cls.provable
    assert not cls.is_ceiling


def test_straightline_cycles_sum_of_constants():
    cfg = _cfg(STRAIGHT)
    # two adds before the return; the return arm holds the count, so jalr is excluded.
    assert cfg.straightline_cycles() == "tadd + tadd"


def test_generate_scaffold_has_program_module_and_functor():
    arms = [(0x0, "00c585b3", "(* add *)"), (0x8, "00008067", "(* jalr *)")]
    module, src = intake.generate_scaffold("sum3", arms, 0x0, [0x8])
    assert module == "sum3_lifted"
    assert "Module TimingProof (cpu : RVCPUTimingBehavior)." in src
    assert "Module Program_sum3 <: ProgramInformation." in src
    assert "Definition entry_addr : N := 0x0." in src
    assert "| 0x0 => 0x00c585b3" in src
    assert "Module RISCVTiming := RISCVTiming cpu Program_sum3." in src
    assert "Module sum3Auto := RISCVTimingAutomation RISCVTiming." in src


def test_straightline_invariant_and_proof():
    cfg = _cfg(STRAIGHT)
    post = "cycle_count_of_trace t' = tadd + tadd"
    inv = intake.straightline_invariant("sum3", cfg, post)
    assert "| 0x0 => Some (cycle_count_of_trace t' = 0)" in inv
    assert f"| 0x8 => Some ({post})" in inv
    proof = intake.straightline_proof()
    assert proof[0].startswith("Local Ltac step")
    assert "destruct_inv 32 PRE." in proof
    assert proof[-1] == intake.STRAIGHTLINE_CLOSER


def test_build_targetspec_requires_dependencies_in_order():
    lr = intake.LiftResult(
        ok=True, func="sum3", ceiling=Ceiling.STRAIGHT_LINE,
        scaffold_module="sum3_lifted", entry_addr=0x0, exit_addrs=[0x8],
        postcondition="cycle_count_of_trace t' = tadd + tadd",
    )
    spec = intake.build_targetspec(lr)
    # RISCVTiming (the module-type source) and NEORV32 (the concrete CPU) must precede the scaffold.
    assert spec.requires == ["NEORV32", "RISCVTiming", "sum3_lifted"]
    assert spec.program_module == "Program_sum3"
    assert spec.auto_module == "sum3Auto"
    assert spec.theorem_name == "sum3_timing_gen"
    assert spec.lifted_program == "lifted_prog"


def test_slice_function_isolates_one_symbol():
    two = STRAIGHT + "\n0000000c <other>:\n   c:\t00008067          \tjalr\tzero,0(ra)\n"
    sliced = intake._slice_function(two, "sum3")
    assert "add\ta1,a1,a2" in sliced
    assert "<other>" not in sliced and sliced.count("jalr") == 1


# Realistic gcc -O2 output: the loop body lives under a compiler-local `<.L3>:` header, separated
# by a blank line. The slicer must keep capturing through it (or every loop looks straight-line).
LOCAL_LABEL_SPLIT = """\
00000000 <asum>:
   0:\t02058463          \tbeq\ta1,zero,28 <.L4>
   4:\t00259593          \tslli\ta1,a1,0x2
  10:\t00000513          \taddi\ta0,zero,0

00000014 <.L3>:
  14:\t0007a703          \tlw\ta4,0(a5)
  18:\t00478793          \taddi\ta5,a5,4
  1c:\t00e50533          \tadd\ta0,a0,a4
  20:\tfed79ae3          \tbne\ta5,a3,14 <.L3>
  24:\t00008067          \tjalr\tzero,0(ra)

00000030 <helper>:
  30:\t00008067          \tjalr\tzero,0(ra)
"""


def test_slice_keeps_local_labels_drops_other_functions():
    sliced = intake._slice_function(LOCAL_LABEL_SPLIT, "asum")
    assert "lw\ta4,0(a5)" in sliced        # loop body under <.L3> is kept
    assert "<helper>" not in sliced         # the next real function is dropped
    # and the recovered CFG actually sees the loop, so it is not misread as straight-line.
    cfg = _cfg(sliced)
    assert cfg.loop_headers == [0x14]
    assert intake.classify(cfg) is Ceiling.ARRAY_POINTER
