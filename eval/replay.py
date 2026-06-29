"""Per-arm gold-proof replay: a ground-truth oracle decoupled from synthesis.

Phase 0 of the proof-search track. The discharge problem is "given a stated theorem, which
closer tactic closes which subgoal." Today that is entangled with synthesis (a model proposes
the invariant AND the orchestrator drives the proof), so when a target fails you cannot tell
whether the invariant was wrong or the closer was. This harness removes synthesis from the loop:
it states the theorem with the target's **gold** invariant (from `targets.yaml`, no LLM), then
replays each individual `gold_proof` arm against the freshly generated scaffold, recording
per-arm whether that arm errored, what goals it left open, and whether the proof finished.

Use it to develop closers for the next curriculum rung (array/stride loops): write the arms, run
`cloq-agent replay <target>`, and read off EXACTLY which arm fails which goal — with the
invariant pinned to ground truth, so a failure is unambiguously a discharge bug, not a synthesis
bug. The pytest (`tests/test_replay_harness.py`) turns every gold proof into a regression: each
arm must close its goal against the scaffold the engine generates today.

Soundness note: this only *replays* a known proof through Rocq; it proves nothing new. Rocq still
decides closure (a stale/edited gold arm that no longer type-checks shows up here as ok=False).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from cloq_agent.proof.petanque_driver import PetanqueDriver
from cloq_agent.proof.theorem_builder import render, write

from .targets import build_spec, load_targets


def _inv_name(invariant_src: str) -> str:
    """Name of the `Definition <name> ...` invariant block (mirrors orchestrator._inv_name, kept
    local so the harness does not pull in the LLM stack)."""
    m = re.search(r"Definition\s+(\w+)", invariant_src)
    return m.group(1) if m else "timing_invs"


@dataclass
class ArmResult:
    index: int               # 0-based position of this arm in the gold_proof list
    tactic: str
    ok: bool                 # the arm ran without a Rocq error / timeout
    finished: bool           # the whole proof was complete after this arm
    goals_before: int        # open goals before the arm
    goals_after: int         # open goals after the arm (== goals_before on failure)
    error: str | None        # Rocq/timeout error when ok=False
    residual: list[str]      # pretty-printed open goals after the arm (the obligation to close)


@dataclass
class ReplayReport:
    target: str
    theorem: str
    scaffold_path: str
    started: bool                       # the theorem elaborated (gold invariant type-checks)
    start_error: str | None
    closed: bool                        # the gold proof reached Qed against the scaffold
    arms: list[ArmResult] = field(default_factory=list)

    @property
    def first_failure(self) -> ArmResult | None:
        return next((a for a in self.arms if not a.ok), None)

    def render(self) -> str:
        lines = [f"replay {self.target} ({self.theorem})"]
        if not self.started:
            lines.append(f"  ✗ start failed (invariant did not elaborate): {self.start_error}")
            return "\n".join(lines)
        for a in self.arms:
            mark = "✓" if a.ok else "✗"
            delta = f"{a.goals_before}->{a.goals_after}" if a.ok else f"{a.goals_before} goals open"
            lines.append(f"  {mark} arm {a.index} [{delta}] {a.tactic}")
            if not a.ok:
                lines.append(f"      error: {a.error}")
                for g in a.residual[:1]:
                    lines.append(f"      residual goal:\n{_indent(g, 8)}")
        verdict = "CLOSED (Qed)" if self.closed else "STALLED"
        lines.append(f"  => {verdict}: {sum(a.ok for a in self.arms)}/{len(self.arms)} arms ran")
        return "\n".join(lines)


def _indent(text: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + ln for ln in text.splitlines())


def replay_gold_arms(
    driver: PetanqueDriver,
    target_name: str,
    *,
    targets_file: str | Path,
    repo_root: Path,
) -> ReplayReport:
    """State `target_name`'s theorem with its GOLD invariant and replay each gold_proof arm,
    one at a time, against the generated scaffold. Returns a per-arm report. Raises ValueError if
    the target has no resolvable gold invariant or no gold proof (nothing to replay)."""
    t = load_targets(str(targets_file))[target_name]
    spec, _desc, _secret, gold_inv, gold_proof, _sk = build_spec(t, repo_root, name=target_name)
    if gold_inv is None:
        raise ValueError(
            f"{target_name}: no resolvable gold_invariant (the replay oracle needs ground truth)"
        )
    if not gold_proof:
        raise ValueError(f"{target_name}: no gold_proof arms to replay")

    workspace = Path(driver.cfg.workspace)
    scaffold = write(spec, render(spec, gold_inv, _inv_name(gold_inv)), workspace)
    start = driver.start(str(scaffold), spec.theorem_name)
    if not start.ok:
        return ReplayReport(target_name, spec.theorem_name, str(scaffold),
                            started=False, start_error=start.error, closed=False)

    arms: list[ArmResult] = []
    cur = start
    for i, tac in enumerate(gold_proof):
        before = len(cur.goals)
        res = driver.run(cur.state, tac)
        if not res.ok:
            # Pinpoint: this arm is the broken closer. Carry the pre-failure goals as the
            # obligation it was meant to discharge.
            arms.append(ArmResult(i, tac, ok=False, finished=False, goals_before=before,
                                  goals_after=before, error=res.error,
                                  residual=[g.pretty for g in cur.goals]))
            break
        arms.append(ArmResult(i, tac, ok=True, finished=res.finished, goals_before=before,
                              goals_after=len(res.goals), error=None,
                              residual=[g.pretty for g in res.goals]))
        cur = res
        if res.finished:
            break

    closed = bool(arms and arms[-1].ok and arms[-1].finished)
    return ReplayReport(target_name, spec.theorem_name, str(scaffold),
                        started=True, start_error=None, closed=closed, arms=arms)


def targets_with_gold_proof(targets_file: str | Path) -> list[str]:
    """Names of every registry target that carries a gold_proof (the replay oracle's corpus)."""
    return [name for name, t in load_targets(str(targets_file)).items() if t.get("gold_proof")]
