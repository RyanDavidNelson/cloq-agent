"""Eval harness: run the agent over every target, collect the metrics from docs/SPEC.md §7."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rich.table import Table

from cloq_agent.config import Config
from cloq_agent.agent.orchestrator import Orchestrator, ProofResult
from cloq_agent.proof.petanque_driver import driver as pet_driver

from .targets import build_spec, load_targets


@dataclass
class EvalReport:
    results: list[ProofResult] = field(default_factory=list)
    started: float = field(default_factory=time.time)

    @property
    def all_passed(self) -> bool:
        return all(r.proved for r in self.results) and bool(self.results)

    def render(self) -> Table:
        tbl = Table(title="cloq-agent eval")
        for col in ("target", "proved", "inv#", "iters", "llm", "esc", "closing", "secs", "note"):
            tbl.add_column(col)
        for r in self.results:
            tbl.add_row(
                r.target,
                "✅" if r.proved else "❌",
                str(r.invariant_attempt),
                str(r.iterations),
                str(r.llm_calls),
                "y" if r.escalated else "-",
                (r.closing_tactic or "")[:18],
                f"{r.wall_s:.1f}",
                (r.error or "")[:32],
            )
        return tbl

    def save(self, out_dir: str | Path) -> Path:
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"eval_{int(self.started)}.json"
        out.write_text(json.dumps([asdict(r) for r in self.results], indent=2))
        return out


def run_eval(cfg: Config, repo_root: Path, only: list[str] | None = None) -> EvalReport:
    targets = load_targets(cfg.eval.targets_file)
    orch = Orchestrator(cfg)
    report = EvalReport()

    with pet_driver(cfg.petanque) as d:
        for name, t in targets.items():
            if only and name not in only:
                continue
            spec, cfg_desc, secret, gold = build_spec(t, repo_root)
            res = orch.prove(d, spec, cfg_description=cfg_desc,
                             secret_param=secret, gold_invariant=gold)
            report.results.append(res)

    report.save(cfg.eval.out_dir)
    return report
