"""dudect.py — fixed-vs-random constant-time test driven by the FPGA mcycle oracle.

Classic dudect methodology: interleave a FIXED secret class and a RANDOM secret class, collect
mcycle samples for each, and run Welch's t-test on the two cycle distributions. |t| above ~4.5
is strong evidence of a data-dependent timing leak. Because we measure exact mcycle on a
deterministic core (caches off, interrupts masked), the distributions are tight and the test is
far more sensitive than wall-clock dudect on a noisy OS.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from measure import Neorv32Oracle


@dataclass
class DudectResult:
    t_statistic: float
    leaky: bool
    n_fixed: int
    n_random: int


def _welch_t(a: list[int], b: list[int]) -> float:
    import statistics as st
    if len(a) < 2 or len(b) < 2:
        return 0.0
    ma, mb = st.mean(a), st.mean(b)
    va, vb = st.variance(a), st.variance(b)
    denom = ((va / len(a)) + (vb / len(b))) ** 0.5
    return 0.0 if denom == 0 else (ma - mb) / denom


def run_dudect(
    oracle: Neorv32Oracle,
    *,
    fixed_secret: int = 0,
    trials: int = 2000,
    threshold: float = 4.5,
) -> DudectResult:
    fixed_cycles: list[int] = []
    random_cycles: list[int] = []
    for _ in range(trials):
        if random.random() < 0.5:
            fixed_cycles.append(oracle.run_once(fixed_secret).mcycle)
        else:
            random_cycles.append(oracle.run_once(random.getrandbits(32)).mcycle)
    t = _welch_t(fixed_cycles, random_cycles)
    return DudectResult(
        t_statistic=t,
        leaky=abs(t) > threshold,
        n_fixed=len(fixed_cycles),
        n_random=len(random_cycles),
    )
