# FPGA oracle (AMD AUP-ZU3)

The oracle grounds every formal proof in silicon: it runs the *same* lifted binary on a real
RISC-V softcore (**NEORV32**) and checks that measured cycle counts match the Cloq prediction,
and that timing is invariant across secrets. This is what makes "not trivially true" enforceable
rather than aspirational.

## Why this board makes it easy

The AUP-ZU3 is a Zynq UltraScale+ (XCZU3EG): a hard quad-core Cortex-A53 **PS** next to FPGA
**PL**. We put NEORV32 in the PL and let the A53 (running PYNQ/Python) be the test harness over
AXI. The whole oracle is self-contained on the board; no desktop serial glue.

```
PS (A53 + PYNQ)            PL (programmable logic)
  measure.py  ──AXI-Lite──▶  NEORV32 (caches off, Zicntr)
   write INPUT,GO                 runs target, brackets with mcycle/minstret
   read MCYCLE,DONE  ◀──         writes results to the mailbox
```

## Determinism rules (must match the Cloq timing model)

- NEORV32 config: `rv32imc_Zicsr_Zicntr`, **caches disabled**, internal IMEM/DMEM.
- Interrupts masked across the measured window (firmware does this).
- One clean PL clock from the PS (`pl_clk0`).
- Pin the NEORV32 commit + SoC config; any timing-model recalibration must trace to a bitstream.

## Build & run

1. Package NEORV32 as Vivado IP (once): in `neorv32/rtl/system_integration`, run
   `source neorv32_vivado_ip.tcl` in the Vivado Tcl console.
2. Build the bitstream: `vivado -mode batch -source vivado/build_neorv32_zynq.tcl -tclargs <neorv32_repo>`.
3. Build measurement firmware per target: `make -C firmware TARGET_OBJ=<target>.o`.
4. On the board: `python host/measure.py --target chacha20_block --sweep-inputs 256`,
   or the dudect leak test via `host/dudect.py`.

## Two checks, three anti-vacuity layers

| Check | Catches |
|---|---|
| `measure.check_wcet` (measured == predicted across inputs) | wrong/loose timing model, degenerate bound |
| `measure.check_constant_time` / `dudect` (mcycle ⟂ secret) | real timing leak; unsound CT proof |
| `minstret` vs proof-trace instruction count | lifted binary ≠ executed binary (build/lift drift) |
| `eval/mutate.py` (inject a leak → proof must break *and* FPGA must vary) | trivially-true spec/harness |
