"""cloq-agent command line: `index`, `prove`, `eval`.

Thin wiring around the library so the same code paths back the CLI, CI, and the eval harness.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from .config import load_config
from .rag.index import build_index

console = Console()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def cmd_index(args) -> int:
    cfg = load_config(args.config)
    root = _repo_root()
    console.print("[bold]Indexing Picinæ/Cloq corpus + solved proofs...[/bold]")
    store = build_index(
        cfg.rag,
        vendor=root / "vendor" / "picinae",
        proofs=root / "proofs",
        runs=root / cfg.eval.out_dir.lstrip("./"),
    )
    console.print(f"Indexed [green]{len(store)}[/green] records -> {cfg.rag.store_dir}")
    return 0


def cmd_prove(args) -> int:
    from .agent.orchestrator import Orchestrator
    from .proof.petanque_driver import driver as pet_driver
    from eval.targets import load_targets, build_spec  # local helper, see eval/targets.py

    cfg = load_config(args.config)
    targets = load_targets(cfg.eval.targets_file)
    if args.target not in targets:
        console.print(f"[red]unknown target '{args.target}'. known: {list(targets)}[/red]")
        return 2
    t = targets[args.target]
    spec, cfg_desc, secret, gold, gold_proof = build_spec(t, _repo_root(), name=args.target)
    orch = Orchestrator(cfg)
    with pet_driver(cfg.petanque) as d:
        res = orch.prove(d, spec, cfg_description=cfg_desc,
                         secret_param=secret, gold_invariant=gold, gold_proof=gold_proof)
    status = "[green]PROVED[/green]" if res.proved else "[red]FAILED[/red]"
    console.print(f"{status} {res.target}  iters={res.iterations} llm_calls={res.llm_calls} "
                  f"closing={res.closing_tactic} {res.wall_s:.1f}s")
    if res.error:
        console.print(f"  [yellow]{res.error}[/yellow]")
    return 0 if res.proved else 1


def cmd_eval(args) -> int:
    from eval.harness import run_eval

    cfg = load_config(args.config)
    report = run_eval(cfg, _repo_root(), only=args.only)
    console.print(report.render())
    return 0 if report.all_passed else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cloq-agent")
    p.add_argument("--config", default=None, help="path to config yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("index", help="build the RAG corpus").set_defaults(func=cmd_index)

    pp = sub.add_parser("prove", help="prove one target end-to-end")
    pp.add_argument("target")
    pp.set_defaults(func=cmd_prove)

    pe = sub.add_parser("eval", help="run the eval harness over all targets")
    pe.add_argument("--only", nargs="*", default=None, help="restrict to these target names")
    pe.set_defaults(func=cmd_eval)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
