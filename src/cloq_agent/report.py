"""Structured diagnostic for a prove-c run, rendered to text / JSON / Markdown / HTML.

Every pipeline stage emits a record so the report shows *where* a run stopped and why, instead of
a stack trace (CLAUDE.md "Conventions"). The stages are:

    compile | lift | classify | spec-lint | invariant | repair | stored

(no FPGA stage — the deferred board path is off the critical line). On success the report carries
the proven cycle-count closed form plus a predicted range; on failure it carries the failing
stage, the relevant log, the last residual goal, and — for a known limitation — the ceiling class,
so an expected failure on a real data-structure loop reads as such rather than as a crash. That
failure path is the common case, so it is first-class here, not an afterthought.
"""
from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

# The ordered pipeline stages a prove-c run passes through (no FPGA stage).
STAGES = ("compile", "lift", "classify", "spec-lint", "invariant", "repair", "stored")


class Status(str, Enum):
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"
    LIMITATION = "limitation"   # a known ceiling case — expected, not an error


# --- NEORV32 predicted-range evaluator -------------------------------------------------
# Concrete per-instruction cycle costs under the vendored NEORV32BaseConfig (fast shift/mul OFF):
# the symbolic t-constants resolve to integers EXCEPT the two memory wait-state parameters,
# T_inst_latency (taken branches / jumps) and T_data_latency (loads/stores), which stay abstract.
# So a straight-line cost is either exact (no memory/branch) or `base + a*T_inst + b*T_data`.
# This is an informational prediction; the Rocq proof of the symbolic closed form is the truth.
_FIXED = {  # cost in cycles, fully determined by NEORV32BaseConfig
    **{m: 2 for m in ("tadd", "taddi", "tsub", "txor", "txori", "tand", "tandi", "tor", "tori",
                      "tlui", "tauipc", "tslt", "tslti", "tsltu", "tsltiu")},
    **{m: 3 for m in ("tfbeq", "tfbne", "tfblt", "tfbge", "tfbltu", "tfbgeu")},
    **{m: 35 for m in ("tmul", "tmulh", "tmulhsu", "tmulhu", "tdiv", "tdivu", "trem", "tremu")},
    **{m: 4 for m in ("tandn", "torn", "txnor", "tmin", "tminu", "tmax", "tmaxu", "tzext")},
    **{m: 3 for m in ("tcsrrw", "tcsrrwi", "tcsrrc", "tcsrrci", "tcsrrs", "tcsrrsi")},
}
# base cost + which abstract latency parameter each adds one of.
_LATENCY = {
    **{m: (5, "T_inst_latency") for m in ("ttbeq", "ttbne", "ttblt", "ttbge", "ttbltu", "ttbgeu",
                                          "tjal", "tjalr")},
    **{m: (4, "T_data_latency") for m in ("tlw", "tlh", "tlhu", "tlb", "tlbu", "tsw", "tsh", "tsb")},
}
_SHIFTS = {"tslli", "tsrli", "tsrai", "tsll", "tsrl", "tsra"}  # 3 + offset (fast shift off)


def neorv32_cycle_range(closed_form: str | None) -> str | None:
    """Turn a straight-line symbolic cycle closed form into a concrete NEORV32 prediction.

    Returns e.g. "4 cycles (exact)" or "12 + 2*T_data_latency cycles (>= 12)", or None if the form
    is parametric (a loop term with `*`/`(`) or contains an unknown token — in which case the caller
    just shows the symbolic form.
    """
    if not closed_form:
        return None
    rhs = closed_form.split("=", 1)[1] if "=" in closed_form else closed_form
    rhs = rhs.strip()
    if not rhs or "(" in rhs or "*" in rhs:
        return None  # parametric / loop form: no flat range
    fixed = 0
    latency: dict[str, int] = {}
    for term in rhs.split("+"):
        toks = term.split()
        if not toks:
            continue
        name = toks[0]
        if name in _FIXED:
            fixed += _FIXED[name]
        elif name in _LATENCY:
            base, var = _LATENCY[name]
            fixed += base
            latency[var] = latency.get(var, 0) + 1
        elif name in _SHIFTS and len(toks) > 1 and toks[1].isdigit():
            fixed += 3 + int(toks[1])
        else:
            return None  # unknown token -> don't fabricate a number
    if not latency:
        return f"{fixed} cycles (exact for NEORV32BaseConfig)"
    parts = [str(fixed)] + [f"{n}*{v}" if n > 1 else v for v, n in sorted(latency.items())]
    return f"{' + '.join(parts)} cycles (>= {fixed}; + memory wait states)"


@dataclass
class StageRecord:
    name: str
    status: Status
    detail: str = ""


@dataclass
class ProveCReport:
    target: str
    func: str
    proved: bool = False
    prop: str = "wcet"          # wcet | ct
    stages: list[StageRecord] = field(default_factory=list)
    # Provenance + headline numbers.
    toolchain_version: str | None = None
    flags: list[str] = field(default_factory=list)
    ceiling_class: str | None = None
    predicted_cycles: str | None = None     # proven/pinned symbolic closed form
    predicted_range: str | None = None      # concrete NEORV32 prediction (see neorv32_cycle_range)
    # Budgets actually consumed (from ProofResult).
    attempts: int = 0
    iterations: int = 0
    llm_calls: int = 0
    added_to_corpus: bool = False
    # Logs + the residual obligation, surfaced on failure.
    compile_log: str = ""
    lift_log: str = ""
    residual_goal: str | None = None
    error: str | None = None
    # Optional hook fired as each stage completes, so a caller (the API) can stream stage
    # transitions live. Not serialised. Exceptions from the hook are swallowed so a flaky
    # consumer can never break the pipeline.
    on_stage: Callable[[StageRecord], None] | None = None

    def stage(self, name: str, status: Status, detail: str = "") -> "ProveCReport":
        rec = StageRecord(name, status, detail)
        self.stages.append(rec)
        if self.on_stage is not None:
            try:
                self.on_stage(rec)
            except Exception:  # a streaming consumer must never break the engine
                pass
        return self

    @property
    def failed_stage(self) -> StageRecord | None:
        for s in self.stages:
            if s.status in (Status.FAILED, Status.LIMITATION):
                return s
        return None

    @property
    def expected_failure(self) -> bool:
        """Not proved, but the stop was a known-ceiling limitation rather than an unexpected break."""
        fs = self.failed_stage
        return not self.proved and fs is not None and fs.status is Status.LIMITATION

    @property
    def headline(self) -> str:
        if self.proved:
            return "PROVED"
        if self.expected_failure:
            return "NOT PROVED (expected failure for this ceiling class)"
        return "NOT PROVED"

    # --- serialisation -------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "target": self.target, "func": self.func, "property": self.prop,
            "proved": self.proved, "headline": self.headline,
            "ceiling_class": self.ceiling_class,
            "predicted_cycles": self.predicted_cycles, "predicted_range": self.predicted_range,
            "toolchain_version": self.toolchain_version, "flags": self.flags,
            "attempts": self.attempts, "iterations": self.iterations, "llm_calls": self.llm_calls,
            "added_to_corpus": self.added_to_corpus,
            "compile_log": self.compile_log, "lift_log": self.lift_log,
            "residual_goal": self.residual_goal, "error": self.error,
            "stages": [{"name": s.name, "status": s.status.value, "detail": s.detail}
                       for s in self.stages],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    # Plain-text status tags (no emoji); fixed width so the stage column lines up.
    _TAG = {
        Status.OK: "[  ok  ]",
        Status.FAILED: "[ FAIL ]",
        Status.SKIPPED: "[ skip ]",
        Status.LIMITATION: "[xfail ]",   # expected failure for a known ceiling class
    }

    def render(self) -> str:
        """A compact human-readable diagnostic (the CLI prints this)."""
        lines = [f"prove-c {self.func}: {self.headline}"]
        if self.ceiling_class:
            lines.append(f"  class: {self.ceiling_class}")
        if self.proved:
            if self.predicted_cycles:
                lines.append(f"  cycle-count closed form: {self.predicted_cycles}")
            if self.predicted_range:
                lines.append(f"  predicted range: {self.predicted_range}")
            lines.append(f"  added to corpus: {'yes' if self.added_to_corpus else 'no'}")
        if self.toolchain_version:
            lines.append(f"  toolchain: {self.toolchain_version.splitlines()[0]}")
        if self.flags:
            lines.append(f"  flags: {' '.join(self.flags)}")
        lines.append(f"  budgets: attempts={self.attempts} iters={self.iterations} "
                     f"llm={self.llm_calls}")
        lines.append("  stages:")
        for s in self.stages:
            suffix = f" - {s.detail}" if s.detail else ""
            lines.append(f"    {self._TAG[s.status]} {s.name}{suffix}")
        if not self.proved and self.residual_goal:
            lines.append(f"  last residual goal: {_oneline(self.residual_goal, 200)}")
        if self.error:
            lines.append(f"  diagnosis: {self.error}")
        return "\n".join(lines)

    def to_markdown(self) -> str:
        md = [f"# prove-c: `{self.func}` - {self.headline}", "",
              f"- **source**: `{self.target}`",
              f"- **property**: {self.prop}"]
        if self.ceiling_class:
            md.append(f"- **class**: {self.ceiling_class}")
        if self.toolchain_version:
            md.append(f"- **toolchain**: `{self.toolchain_version.splitlines()[0]}`")
        if self.flags:
            md.append(f"- **flags**: `{' '.join(self.flags)}`")
        md.append(f"- **budgets**: attempts={self.attempts}, iterations={self.iterations}, "
                  f"llm_calls={self.llm_calls}")
        if self.proved:
            md += ["", "## Result", ""]
            md.append(f"- **cycle-count closed form**: `{self.predicted_cycles or 'n/a'}`")
            md.append(f"- **predicted range**: {self.predicted_range or 'n/a'}")
            md.append(f"- **added to corpus**: {'yes' if self.added_to_corpus else 'no'}")
        md += ["", "## Stages", "", "| stage | status | detail |", "|---|---|---|"]
        for s in self.stages:
            md.append(f"| {s.name} | {s.status.value} | {s.detail or ''} |")
        if not self.proved:
            md += ["", "## Diagnosis", ""]
            if self.error:
                md.append(self.error)
            if self.residual_goal:
                md += ["", "**Last residual goal:**", "", "```", self.residual_goal, "```"]
            if self.compile_log.strip():
                md += ["", "<details><summary>compile log</summary>", "",
                       "```", self.compile_log.strip(), "```", "", "</details>"]
        return "\n".join(md) + "\n"

    def to_html(self) -> str:
        def esc(x: object) -> str:
            return html.escape(str(x))

        rows = "\n".join(
            f"<tr class='{s.status.value}'><td>{esc(s.name)}</td>"
            f"<td>{esc(s.status.value)}</td><td>{esc(s.detail)}</td></tr>"
            for s in self.stages
        )
        result = ""
        if self.proved:
            result = (
                "<h2>Result</h2><ul>"
                f"<li><b>cycle-count closed form:</b> <code>{esc(self.predicted_cycles or 'n/a')}</code></li>"
                f"<li><b>predicted range:</b> {esc(self.predicted_range or 'n/a')}</li>"
                f"<li><b>added to corpus:</b> {'yes' if self.added_to_corpus else 'no'}</li></ul>"
            )
        diag = ""
        if not self.proved:
            goal = (f"<h3>Last residual goal</h3><pre>{esc(self.residual_goal)}</pre>"
                    if self.residual_goal else "")
            clog = (f"<details><summary>compile log</summary><pre>{esc(self.compile_log.strip())}</pre>"
                    "</details>" if self.compile_log.strip() else "")
            diag = f"<h2>Diagnosis</h2><p>{esc(self.error or '')}</p>{goal}{clog}"
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>prove-c {esc(self.func)}</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:2rem;max-width:60rem}}
 table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #ccc;padding:.4rem .6rem;text-align:left}}
 tr.ok td{{background:#eaf7ea}} tr.failed td{{background:#fbeaea}}
 tr.limitation td{{background:#fff6e0}} tr.skipped td{{background:#f2f2f2}}
 code,pre{{background:#f6f8fa;padding:.1rem .3rem;border-radius:4px}} pre{{padding:.6rem;overflow:auto}}
</style></head><body>
<h1>prove-c: <code>{esc(self.func)}</code> &mdash; {esc(self.headline)}</h1>
<ul>
 <li><b>source:</b> <code>{esc(self.target)}</code></li>
 <li><b>property:</b> {esc(self.prop)}</li>
 {f'<li><b>class:</b> {esc(self.ceiling_class)}</li>' if self.ceiling_class else ''}
 {f'<li><b>toolchain:</b> <code>{esc(self.toolchain_version.splitlines()[0])}</code></li>' if self.toolchain_version else ''}
 {f"<li><b>flags:</b> <code>{esc(' '.join(self.flags))}</code></li>" if self.flags else ''}
 <li><b>budgets:</b> attempts={self.attempts}, iterations={self.iterations}, llm_calls={self.llm_calls}</li>
</ul>
{result}
<h2>Stages</h2>
<table><tr><th>stage</th><th>status</th><th>detail</th></tr>
{rows}
</table>
{diag}
</body></html>
"""


def _oneline(text: str, n: int) -> str:
    return re.sub(r"\s+", " ", text).strip()[:n]
