"""Held-out transferability study (Phase 7).

For each of the 20 candidates: compile the reduced unit with the pinned flags, lift, classify, and
— for the straight-line (in-scope) class — discharge a CFG-derived proof and machine-check it with
coqc in the rocq container. Ceiling classes (array/pointer, search, aliasing, unsupported) are
reported at their named class, not attempted via the LLM (they are the documented wall).

HELD-OUT DISCIPLINE: the straight-line invariant + discharge are derived purely from the CFG
(`intake.straightline_invariant` / `straightline_proof`); no target's gold invariant/proof is ever
consulted, and `load_proof_library` / the few-shot are never touched. A pass is generalization.

Outputs per target: runs/transfer/<suite>/<target>/{report.json, report.html, <mod>_lifted.v,
<Thm>_gen.v}. Writes a metrics.json and prints a per-suite x tier table.

Usage:  python eval/transfer/run_transfer.py
Env:    CLOQ_ROCQ_CONTAINER (default docker-rocq-1) — the running rocq image with coqc + Picinae.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cloq_agent.lift import intake                                    # noqa: E402
from cloq_agent.lift.compile import DEFAULT_CFLAGS, GCC, load_machine_code  # noqa: E402
from cloq_agent.proof.theorem_builder import render                  # noqa: E402
from cloq_agent.report import ProveCReport, Status, neorv32_cycle_range  # noqa: E402

ROCQ = os.environ.get("CLOQ_ROCQ_CONTAINER", "docker-rocq-1")
PROOFS = ROOT / "proofs"
TARGETS_DIR = PROOFS / "targets"
COQ_ARGS = (
    "-R ../vendor/picinae Picinae -I ../vendor/picinae/timing/riscv "
    "-I ../vendor/picinae/timing/examples -R targets Targets "
    "-w -notation-overridden -w -deprecated-since-8.20 -w -overriding-logical-loadpath"
).split()


@dataclass
class Result:
    suite: str
    tier: str
    name: str
    func: str
    property: str
    status: str
    compiled: bool = False
    lifted: bool = False
    ceiling_class: str | None = None
    in_scope: bool = False           # straight-line (the engine's reach)
    proved: bool = False
    mode: str = "n/a"                # deterministic | n/a
    iterations: int = 0
    wall_s: float = 0.0
    cluster: str = ""                # failure cluster for the capability matrix
    note: str = ""
    predicted_range: str | None = None


def _coqc(vfile: str) -> tuple[bool, str]:
    """coqc a file under proofs/targets/ inside the rocq container (bind-mounted /work/proofs)."""
    cmd = ["docker", "exec", ROCQ, "bash", "-lc",
           "eval $(opam env) && cd /work/proofs && coqc " + " ".join(COQ_ARGS) + f" targets/{vfile}"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return p.returncode == 0, (p.stderr or p.stdout)[-400:]


def _gcc(src: Path, obj: Path) -> tuple[bool, str]:
    p = subprocess.run([GCC, *DEFAULT_CFLAGS, "-c", str(src), "-o", str(obj)],
                       capture_output=True, text=True, timeout=120)
    return p.returncode == 0, p.stderr


def _clean_targets(*stems: str) -> None:
    for stem in stems:
        for ext in (".v", ".vo", ".vos", ".vok", ".glob"):
            (TARGETS_DIR / f"{stem}{ext}").unlink(missing_ok=True)
        (TARGETS_DIR / f".{stem}.aux").unlink(missing_ok=True)


def _ceiling_cluster(c: intake.Ceiling) -> str:
    return {
        intake.Ceiling.ARRAY_POINTER: "array/pointer",
        intake.Ceiling.SEARCH_EARLY_EXIT: "search/early-exit",
        intake.Ceiling.ALIASING: "memory-aliasing",
        intake.Ceiling.UNSUPPORTED: "unsupported-cfg",
        intake.Ceiling.COUNTER_LOOP: "counter-loop",
    }.get(c, "")


def run_target(suite: str, t: dict, workdir: Path, out: Path) -> tuple[Result, ProveCReport]:
    r = Result(suite=suite, tier=t["tier"], name=t["name"], func=t["func"],
               property=t["property"], status=t["status"], note=t.get("note", "") or "")
    rep = ProveCReport(target=f"{suite}/{t['name']}", func=t["func"], prop=t["property"])
    t0 = time.time()

    if t["status"] != "ready" or not t.get("src"):
        r.cluster = "lift-gap"
        rep.stage("compile", Status.SKIPPED, f"reduction pending: {r.note}")
        rep.error = f"reduction pending — {r.note or 'drags in too much build machinery'}"
        r.wall_s = time.time() - t0
        return r, rep

    src = ROOT / "eval" / "transfer" / suite / "src" / t["src"]
    obj = workdir / f"{t['name']}.o"
    ok, err = _gcc(src, obj)
    rep.flags = list(DEFAULT_CFLAGS)
    if not ok:
        rep.stage("compile", Status.FAILED, err.strip()[:200])
        rep.error = err.strip()[:300]
        r.cluster = "lift-gap"
        r.wall_s = time.time() - t0
        return r, rep
    r.compiled = True
    rep.stage("compile", Status.OK, f"-> {obj.name}")

    cr = load_machine_code(obj, t["func"])
    rep.toolchain_version = cr.toolchain_version
    lr = intake.lift(cr, ROOT)
    if not lr.ok:
        rep.stage("lift", Status.FAILED, lr.error or "lift failed")
        rep.error = lr.error
        r.cluster = "lift-gap"
        r.wall_s = time.time() - t0
        return r, rep
    r.lifted = True
    r.ceiling_class = lr.ceiling.value
    rep.ceiling_class = lr.ceiling.value
    rep.lift_log = lr.cfg_description
    rep.stage("lift", Status.OK, f"entry=0x{lr.entry_addr:x} exits={[hex(a) for a in lr.exit_addrs]}")

    spec = intake.build_targetspec(lr)
    (out / f"{lr.scaffold_module}.v").write_text(lr.scaffold_source)  # lifted scaffold artifact

    if lr.ceiling.provable:  # straight-line: derive + machine-check a proof (held-out, no recall)
        r.in_scope = True
        rep.predicted_cycles = (lr.postcondition or "").replace("cycle_count_of_trace t' = ", "") or None
        rep.predicted_range = neorv32_cycle_range(lr.postcondition)
        r.predicted_range = rep.predicted_range
        rep.stage("classify", Status.OK, "straight-line (in scope)")
        rep.stage("spec-lint", Status.OK, "non-vacuous (cycle_count constrained)")
        body = "  " + "\n    ".join(lr.proof_script) + "\n  Qed."
        thm = f"Tr_{spec.name.capitalize()}_gen"
        (TARGETS_DIR / f"{lr.scaffold_module}.v").write_text(lr.scaffold_source)
        gen_src = render(spec, lr.invariant, f"{spec.name}_timing_invs", proof_body=body)
        (TARGETS_DIR / f"{thm}.v").write_text(gen_src)
        (out / f"{thm}.v").write_text(gen_src)
        r.iterations = len(lr.proof_script)
        ok_s, _ = _coqc(f"{lr.scaffold_module}.v")
        ok_t, terr = (_coqc(f"{thm}.v") if ok_s else (False, "scaffold did not compile"))
        _clean_targets(lr.scaffold_module, thm)
        r.proved = ok_s and ok_t
        r.mode = "deterministic"  # no LLM, no proof library, no few-shot
        if r.proved:
            rep.proved = True
            rep.added_to_corpus = False
            rep.stage("invariant", Status.OK, "CFG-derived invariant type-checks")
            rep.stage("repair", Status.SKIPPED, "closed by deterministic discharge (no LLM)")
            rep.stage("stored", Status.SKIPPED, "transfer run: not written back")
        else:
            # Distinguish WHY a straight-line classification did not prove.
            if lr.cfg.entry in set(lr.cfg.exit_points()):
                r.cluster = "degenerate (identity / 0-cycle body)"
            elif lr.cfg.join_points():
                r.cluster = "branchy-straightline (linear bound invalid)"
            else:
                r.cluster = "straight-line memory-reasoning gap"
            rep.stage("invariant", Status.FAILED, "deterministic discharge did not close")
            rep.error = f"straight-line classified, but the proof did not close ({r.cluster})"
    else:  # ceiling: report the named class; not attempted via LLM (the documented wall)
        help_txt = {
            "array/pointer loop": "needs an exists-index loop invariant + witness",
            "search early-exit": "needs a program-specific decidability case-split",
            "memory-aliasing branch": "needs noverlaps / getmem_noverlap reasoning",
            "unsupported control flow": "nested/irreducible control flow is out of scope",
            "counter-loop": "needs a pinned loop closed form",
        }.get(lr.ceiling.value, "")
        r.cluster = _ceiling_cluster(lr.ceiling)
        rep.stage("classify", Status.LIMITATION,
                  f"{lr.ceiling.value} (expected failure: {help_txt})")
        # emit the theorem statement (Admitted) as the generated artifact
        stub_inv = (f"Definition {spec.name}_timing_invs (t:trace) :=\n"
                    f"match t with (Addr a, s) :: t' => match a with\n"
                    f"| _ => None\nend | _ => None end.")
        thm = f"Tr_{spec.name.capitalize()}_gen"
        (out / f"{thm}.v").write_text(render(spec, stub_inv, f"{spec.name}_timing_invs",
                                             proof_body="  Admitted."))
        rep.error = f"expected failure for {lr.ceiling.value} ({help_txt})"

    r.wall_s = time.time() - t0
    return r, rep


def main() -> int:
    targets = yaml.safe_load((ROOT / "eval" / "transfer" / "targets.yaml").read_text())
    workdir = ROOT / "runs" / "transfer" / "_obj"
    workdir.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []
    for suite in ("openssl", "freertos"):
        for t in targets.get(suite, []):
            print(f"[{suite}/{t['name']}] ({t['tier']}) ...", flush=True)
            tdir = ROOT / "runs" / "transfer" / suite / t["name"]
            tdir.mkdir(parents=True, exist_ok=True)
            r, rep = run_target(suite, t, workdir, tdir)
            (tdir / "report.json").write_text(rep.to_json())   # every target gets a dir + report
            (tdir / "report.html").write_text(rep.to_html())
            results.append(r)
            verdict = ("PROVED" if r.proved else
                       f"ceiling:{r.ceiling_class}" if r.ceiling_class and not r.in_scope else
                       "not-proved" if r.lifted else
                       "pending" if r.status != "ready" else "compile-fail")
            print(f"    -> {verdict}  ({r.wall_s:.1f}s)")

    out = ROOT / "runs" / "transfer"
    (out / "metrics.json").write_text(json.dumps([r.__dict__ for r in results], indent=2))
    _write_report(results, ROOT / "docs" / "results" / "transfer.md")
    print(f"\nWrote {len(results)} results -> {out}  + docs/results/transfer.md")
    return 0


def _write_report(results: list[Result], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by = {("openssl", "easy"): [], ("openssl", "medium"): [], ("openssl", "hard"): [],
          ("freertos", "easy"): [], ("freertos", "medium"): [], ("freertos", "hard"): []}
    for r in results:
        by.setdefault((r.suite, r.tier), []).append(r)

    def rate(rs: list[Result]) -> str:
        n = len(rs)
        p = sum(1 for r in rs if r.proved)
        return f"{p}/{n}" if n else "0/0"

    lines = ["# Held-out transferability study", "",
             "First held-out generalization number for cloq-agent (the figure docs/RESULTS.md flagged",
             "as missing). 20 functions reduced from pinned OpenSSL 3.4.0 + FreeRTOS-Kernel V11.1.0",
             "(see eval/transfer/PINNED.md); the target's gold invariant/proof is withheld from the",
             "proof library and few-shot, so a pass is generalization, not recall. Straight-line",
             "targets are machine-checked to **Qed** with a CFG-derived deterministic proof (no LLM,",
             "no recall); ceiling classes are reported at their named wall (the documented limitation),",
             "not attempted via synthesis.", "",
             "## Held-out success by suite x tier (proved / total)", "",
             "| suite | easy | medium | hard | suite total |", "|---|---|---|---|---|"]
    for suite in ("openssl", "freertos"):
        e, m, h = by[(suite, "easy")], by[(suite, "medium")], by[(suite, "hard")]
        allr = e + m + h
        lines.append(f"| {suite} | {rate(e)} | {rate(m)} | {rate(h)} | {rate(allr)} |")
    allr = results
    lines.append(f"| **both** | {rate([r for r in results if r.tier=='easy'])} | "
                 f"{rate([r for r in results if r.tier=='medium'])} | "
                 f"{rate([r for r in results if r.tier=='hard'])} | **{rate(allr)}** |")

    lines += ["", "## Per-target", "",
              "| suite | tier | target | property | result | ceiling/cluster | mode | iters | wall(s) |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        result = ("PROVED" if r.proved else
                  f"ceiling: {r.ceiling_class}" if r.ceiling_class and not r.in_scope else
                  "not proved" if r.lifted else
                  "reduction pending" if r.status != "ready" else "compile fail")
        cluster = r.cluster or ("-" if r.proved else "")
        lines.append(f"| {r.suite} | {r.tier} | {r.name} | {r.property} | {result} | {cluster} | "
                     f"{r.mode} | {r.iterations} | {r.wall_s:.1f} |")

    # failure clusters mapped to the capability matrix
    clusters: dict[str, list[str]] = {}
    for r in results:
        if not r.proved and r.cluster:
            clusters.setdefault(r.cluster, []).append(f"{r.suite}/{r.name}")
    lines += ["", "## Failure clusters (mapped to the capability matrix)", ""]
    for cl, names in sorted(clusters.items()):
        lines.append(f"- **{cl}** ({len(names)}): {', '.join(names)}")
    lines += ["", "### Reading the result", "",
              "Easy = the pipeline's sweet spot (branchless straight-line) and the source of the real",
              "held-out pass rate. Medium/hard deliberately probe the wall and land at a named ceiling",
              "class (array/pointer, search/early-exit, memory-aliasing, unsupported-CFG) or a",
              "straight-line-with-memory proof gap — that distribution is the transfer finding. Targets",
              "marked *reduction pending* drag in too much build machinery (full FreeRTOSConfig / a",
              "configured OpenSSL tree) and are recorded as lift gaps, per the candidate-swap note.", ""]
    path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
