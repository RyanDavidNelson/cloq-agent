"""cloq-agent command line: `index`, `prove`, `eval`.

Thin wiring around the library so the same code paths back the CLI, CI, and the eval harness.
"""
from __future__ import annotations

import argparse
import logging
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
    # --synthesis-mode overrides the config default (skeleton|freeform) for this run, so the
    # two paths can be A/B'd on the same target without editing config.
    if args.synthesis_mode:
        cfg.agent.synthesis_mode = args.synthesis_mode
    spec, cfg_desc, secret, gold, gold_proof, skeleton = build_spec(
        t, _repo_root(), name=args.target
    )

    # Model preflight: only the synthesis path needs the LLM. If this target has no gold
    # hints, fail loudly *now* if the model server is unreachable rather than mid-run.
    on_gold_path = gold is not None or gold_proof is not None
    if not on_gold_path:
        from .models import LLM
        try:
            LLM(cfg.model).healthcheck()
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            return 2

    orch = Orchestrator(cfg)
    with pet_driver(cfg.petanque) as d:
        res = orch.prove(d, spec, cfg_description=cfg_desc,
                         secret_param=secret, gold_invariant=gold, gold_proof=gold_proof,
                         invariant_skeleton=skeleton)
    status = "[green]PROVED[/green]" if res.proved else "[red]FAILED[/red]"
    console.print(f"{status} {res.target}  iters={res.iterations} llm_calls={res.llm_calls} "
                  f"closing={res.closing_tactic} {res.wall_s:.1f}s")
    if res.error:
        console.print(f"  [yellow]{res.error}[/yellow]")
    return 0 if res.proved else 1


def cmd_doctor(args) -> int:
    """Preflight the model server: a tiny completion that fails loudly if unreachable."""
    from .models import LLM

    cfg = load_config(args.config)
    console.print(f"[bold]Pinging model[/bold] '{cfg.model.name}' at {cfg.model.base_url} ...")
    try:
        reply = LLM(cfg.model).healthcheck()
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        return 1
    console.print(f"[green]ok[/green] model reachable (reply: {reply!r})")
    return 0


def cmd_eval(args) -> int:
    from eval.harness import run_eval
    from eval.targets import resolve_selectors

    cfg = load_config(args.config)
    # Positional selectors and --only both feed the filter; a selector may be a named group
    # (e.g. `eval list_easy_four`) which expands to its targets, or a bare target name.
    selectors = (args.selectors or []) + (args.only or [])
    only = resolve_selectors(cfg.eval.targets_file, selectors)
    report = run_eval(cfg, _repo_root(), only=only)
    console.print(report.render())
    return 0 if report.all_passed else 1


def main(argv: list[str] | None = None) -> int:
    # Surface cloq_agent's own INFO logs (e.g. the proposed invariant) without turning on
    # the verbose INFO chatter of dependencies like pytanque.
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    _agent_log = logging.getLogger("cloq_agent")
    _agent_log.setLevel(logging.INFO)
    _agent_log.addHandler(_h)
    _agent_log.propagate = False

    p = argparse.ArgumentParser(prog="cloq-agent")
    p.add_argument("--config", default=None, help="path to config yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("index", help="build the RAG corpus").set_defaults(func=cmd_index)

    sub.add_parser("doctor", help="preflight the model server").set_defaults(func=cmd_doctor)

    pp = sub.add_parser("prove", help="prove one target end-to-end")
    pp.add_argument("target")
    pp.add_argument("--synthesis-mode", choices=["skeleton", "freeform"], default=None,
                    help="override the invariant-synthesis mode for this run (default: config)")
    pp.set_defaults(func=cmd_prove)

    pe = sub.add_parser("eval", help="run the eval harness over a group/targets (default: all)")
    pe.add_argument("selectors", nargs="*", default=None,
                    help="group name (e.g. list_easy_four) or target names; empty = all targets")
    pe.add_argument("--only", nargs="*", default=None, help="additional target names to restrict to")
    pe.set_defaults(func=cmd_eval)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
