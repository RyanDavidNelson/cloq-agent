"""Regression gate for the nightly eval.

`cloq-agent eval <group>` exits non-zero whenever *any* target in the group fails — but our
groups are *expected* to be partially red (`list_easy_four` is 3/4, `loop_easy` is 1/3; the
remaining targets are documented ceiling cases in CLAUDE.md). So a bare exit code can't tell a
real regression from the known limitations.

This gate pins the set of targets that are *currently expected to prove* and fails only when one
of them stops proving. New, unexpected passes are reported but never fail the gate — they mean
the pin should be tightened, not that the build is broken.

Run after the eval has written its JSON report:

    cloq-agent eval list_easy_four loop_easy || true
    python eval/regression_gate.py            # reads the newest runs/eval_*.json

Override the report location with `--report <file>` or `CLOQ_EVAL_OUT_DIR`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# The pinned pass set. Keep in sync with CLAUDE.md "Measured" line:
#   list_easy_four -> 3/4 (uxListRemove_llm is the known failure)
#   loop_easy      -> 1/3 (only addloop_llm passes; ct_swap / find_in_array are ceiling cases)
EXPECTED_PASS: frozenset[str] = frozenset(
    {
        "vListInitialise_llm",
        "vListInitialiseItem_llm",
        "vListInsertEnd_llm",
        "addloop_llm",
    }
)


def _latest_report(out_dir: Path) -> Path:
    reports = sorted(out_dir.glob("eval_*.json"))
    if not reports:
        sys.exit(f"regression-gate: no eval_*.json under {out_dir} — did the eval run?")
    return reports[-1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="nightly eval regression gate")
    ap.add_argument("--report", type=Path, default=None, help="path to an eval_*.json report")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path(os.environ.get("CLOQ_EVAL_OUT_DIR", "runs")),
        help="directory holding eval_*.json (default: runs/ or $CLOQ_EVAL_OUT_DIR)",
    )
    args = ap.parse_args(argv)

    report_path = args.report or _latest_report(args.out_dir)
    results = json.loads(report_path.read_text())
    proved = {r["target"] for r in results if r.get("proved")}
    seen = {r["target"] for r in results}

    regressed = sorted(t for t in EXPECTED_PASS if t in seen and t not in proved)
    missing = sorted(t for t in EXPECTED_PASS if t not in seen)
    new_passes = sorted(proved - EXPECTED_PASS)

    print(f"regression-gate: report = {report_path}")
    print(f"  proved ({len(proved)}): {', '.join(sorted(proved)) or '-'}")
    if new_passes:
        print(f"  NOTE: unexpected new passes (tighten the pin): {', '.join(new_passes)}")

    if missing:
        print(f"  FAIL: expected-pass targets absent from report: {', '.join(missing)}")
    if regressed:
        print(f"  FAIL: REGRESSION — these used to prove and now don't: {', '.join(regressed)}")

    if regressed or missing:
        return 1
    print(f"  OK: all {len(EXPECTED_PASS)} pinned targets still prove.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
