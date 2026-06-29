"""Held-out search measurement: run the Tier-C search functions (se_find_eq, se_find_ge) through
the deterministic front of the pipeline — compile -> lift -> classify -> shape/timing — and print
where the program-half is ready and where the search-class scaffolding stalls. No model, no
pet-server; needs the riscv cross-toolchain on PATH (host) to compile.

    python eval/heldout/measure.py

This exists to keep the held-out claim honest: find_in_array_tmpl is an identical-program twin of
find_in_array (reused program + reused time_of_* + renamed gold leaves), so it measures the emission
MECHANISM, not generalization. These functions are fresh programs the corpus has never seen, so a
Qed here would measure generalization. The measurement records the gaps between here and there.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

from cloq_agent.lift import intake                                       # noqa: E402
from cloq_agent.lift.cfg import build_cfg, parse_objdump                 # noqa: E402
from cloq_agent.lift.compile import compile_c                            # noqa: E402

TARGETS = [("se_find_eq.c", "se_find_eq"), ("se_find_ge.c", "se_find_ge")]


def measure(cfile: str, func: str) -> None:
    print(f"\n===== {func} =====")
    cr = compile_c(_REPO / "eval" / "heldout" / cfile, func)
    print("compile ok:", cr.ok)
    if not cr.ok:
        print("  compile error:", cr.error or (cr.stderr or "")[:200])
        return
    cfg = build_cfg(parse_objdump(cr.objdump))
    headers = cfg.loop_headers
    print("classify ->", intake.classify(cfg).value, "| loop headers:", [hex(h) for h in headers])
    if headers:
        h = headers[0]
        print("data_dependent_exit:", cfg.data_dependent_exit(h))
        print("array_search_shape:", cfg.array_search_shape(h),
              "   <- None == shape recovery FAILS on the gcc -O2 running-pointer form")
        lt = cfg.loop_timing(h)
        if lt:
            print("loop_timing body (SINGLE, no found/not-found disjunction):", lt[1])
    lr = intake.lift(cr, _REPO, prop="wcet")
    print("lift ok:", lr.ok, "| ceiling:", lr.ceiling.value if lr.ceiling else None,
          "| generated program module:", lr.scaffold_module, "  <- program-half READY")


if __name__ == "__main__":
    for c, f in TARGETS:
        measure(c, f)
