"""measure.py — host-side FPGA oracle, runs on the AUP-ZU3's A53 PS under PYNQ.

Loads the NEORV32 overlay, sweeps inputs through the AXI mailbox, reads back mcycle/minstret,
and compares against the Cloq-predicted closed form. Two checks (docs/SPEC.md §4):
  * WCET / tightness  : measured == predicted(input) across the sweep   (anti-vacuity #1)
  * constant-time     : mcycle invariant across the *secret*            (feeds dudect.py)

This module imports `pynq` only on the board; on a dev workstation it is import-guarded so the
rest of the package still loads.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

try:
    from pynq import Overlay, allocate  # type: ignore  # noqa: F401
    _HAVE_PYNQ = True
except Exception:
    _HAVE_PYNQ = False

# mailbox offsets — keep in sync with fpga/firmware/mailbox.h
INPUT, GO, DONE, MCYCLE, MINSTRET, RESULT = 0x00, 0x04, 0x08, 0x0C, 0x10, 0x14


@dataclass
class Measurement:
    input: int
    mcycle: int
    minstret: int
    result: int


@dataclass
class OracleReport:
    target: str
    agrees: bool
    summary: str
    measurements: list[Measurement]


class Neorv32Oracle:
    def __init__(self, bitstream: str, base: str = "neorv32_0"):
        if not _HAVE_PYNQ:
            raise RuntimeError("pynq not available; run this on the AUP-ZU3 PS")
        self.ol = Overlay(bitstream)
        self.mmio = getattr(self.ol, base).mmio  # AXI-Lite window of the NEORV32 mailbox

    def run_once(self, x: int, *, timeout_s: float = 2.0) -> Measurement:
        self.mmio.write(INPUT, x & 0xFFFFFFFF)
        self.mmio.write(GO, 1)
        deadline = time.time() + timeout_s
        while self.mmio.read(DONE) == 0:
            if time.time() > deadline:
                raise TimeoutError("NEORV32 did not signal DONE")
        return Measurement(
            input=x,
            mcycle=self.mmio.read(MCYCLE),
            minstret=self.mmio.read(MINSTRET),
            result=self.mmio.read(RESULT),
        )

    def sweep(self, inputs: list[int]) -> list[Measurement]:
        return [self.run_once(x) for x in inputs]


def check_wcet(
    oracle: Neorv32Oracle,
    target: str,
    inputs: list[int],
    predicted,                 # callable(input:int) -> int   (the Cloq closed form)
    tolerance: int = 0,
) -> OracleReport:
    ms = oracle.sweep(inputs)
    bad = [(m.input, m.mcycle, predicted(m.input))
           for m in ms if abs(m.mcycle - predicted(m.input)) > tolerance]
    agrees = not bad
    summary = "measured == predicted across sweep" if agrees else \
        f"{len(bad)}/{len(ms)} mismatched, e.g. {bad[0]}"
    return OracleReport(target=target, agrees=agrees, summary=summary, measurements=ms)


def check_constant_time(
    oracle: Neorv32Oracle,
    target: str,
    secret_inputs: list[int],
) -> OracleReport:
    ms = oracle.sweep(secret_inputs)
    cycles = {m.mcycle for m in ms}
    agrees = len(cycles) == 1
    summary = (f"constant: {cycles.pop()} cycles for all secrets" if agrees
               else f"VARIANCE across secrets: {sorted(cycles)[:5]}...")
    return OracleReport(target=target, agrees=agrees, summary=summary, measurements=ms)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bitstream", default="build/neorv32_zynq.bit")
    ap.add_argument("--target", required=True)
    ap.add_argument("--sweep-inputs", type=int, default=64)
    args = ap.parse_args()

    oracle = Neorv32Oracle(args.bitstream)
    inputs = list(range(args.sweep_inputs))
    # default predicted: identity placeholder; real targets pass their Cloq closed form
    rep = check_constant_time(oracle, args.target, inputs)
    print(f"[{rep.target}] agrees={rep.agrees} :: {rep.summary}")
    return 0 if rep.agrees else 1


if __name__ == "__main__":
    raise SystemExit(main())
