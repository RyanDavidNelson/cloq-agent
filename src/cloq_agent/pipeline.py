"""The prove-c pipeline as one reusable function: compile -> lift -> classify -> prove.

This is the single source of truth shared by the CLI (`cloq-agent prove-c`) and the API worker, so
the report a `curl` upload gets is byte-for-byte the report the CLI produces for the same input.
The function only *builds* a `ProveCReport`; it does not print or write files — the caller decides
(the CLI prints + persists; the API streams + returns). Pass `on_stage` to observe stage
transitions live (the API pushes them onto the job's event stream).

Soundness is unchanged: this is glue over the existing engine (compile.py, lift/intake.py, the
orchestrator); Rocq still decides what is proved.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .config import Config
from .lift import intake
from .lift.compile import compile_c, load_machine_code, sanitize_ident
from .report import ProveCReport, StageRecord, Status, neorv32_cycle_range

# Per-ceiling-class explanation surfaced in the diagnostic (CLAUDE.md ceiling section).
CEILING_HELP = {
    intake.Ceiling.COUNTER_LOOP: "provable in principle, but needs a pinned loop closed form "
                                 "(counter-loop synthesis is the proof-search research track)",
    intake.Ceiling.ARRAY_POINTER: "needs an exists-index loop invariant + witness",
    intake.Ceiling.SEARCH_EARLY_EXIT: "needs a program-specific decidability case-split",
    intake.Ceiling.ALIASING: "needs noverlaps / getmem_noverlap memory-aliasing reasoning",
    intake.Ceiling.UNSUPPORTED: "nested/irreducible control flow is out of scope",
}


def run_prove_machine_code(
    *,
    mc_path: str | Path,
    func: str | None = None,
    cfg: Config,
    repo_root: Path,
    mcu: str = "neorv32",
    prop: str = "wcet",
    secret: str | None = None,
    on_stage: Callable[[StageRecord], None] | None = None,
) -> ProveCReport:
    """Disassemble an uploaded machine-code artifact (no compile step), then lift -> classify ->
    prove. This is the GUI/API intake: any RISC-V ELF/object in, a structured report out."""
    mc_path = Path(mc_path)
    name = sanitize_ident(func or mc_path.stem)
    rep = ProveCReport(target=str(mc_path), func=name, prop=prop)
    rep.on_stage = on_stage

    # --- disassemble stage (replaces compile: the input is already machine code) ---
    compiled = load_machine_code(mc_path, name)
    rep.toolchain_version = compiled.toolchain_version
    rep.flags = []
    if not compiled.ok:
        rep.stage("disassemble", Status.FAILED, compiled.error or "disassembly failed")
        rep.error = compiled.error
        return rep
    rep.stage("disassemble", Status.OK, f"{mcu}: {len(_lines(compiled.objdump))} instructions")
    return _prove_from_compiled(rep, compiled, cfg=cfg, repo_root=repo_root, prop=prop, secret=secret)


def run_prove_c(
    *,
    c_path: str | Path,
    func: str,
    cfg: Config,
    repo_root: Path,
    prop: str = "wcet",
    secret: str | None = None,
    on_stage: Callable[[StageRecord], None] | None = None,
) -> ProveCReport:
    """Compile a C unit, then lift -> classify -> prove (the CLI `prove-c` intake)."""
    c_path = Path(c_path)
    rep = ProveCReport(target=str(c_path), func=func, prop=prop)
    rep.on_stage = on_stage

    # --- compile stage ---
    compiled = compile_c(c_path, func)
    rep.toolchain_version = compiled.toolchain_version
    rep.flags = compiled.flags
    rep.compile_log = compiled.stderr or ""
    if not compiled.ok:
        rep.stage("compile", Status.FAILED, compiled.error or "compile failed")
        rep.error = compiled.stderr.strip() or compiled.error
        return rep
    rep.stage("compile", Status.OK, f"-> {compiled.obj_path.name}")
    return _prove_from_compiled(rep, compiled, cfg=cfg, repo_root=repo_root, prop=prop, secret=secret)


def _lines(listing: str | None) -> list[str]:
    from .lift.cfg import parse_objdump

    return [str(i.addr) for i in parse_objdump(listing or "")]


def _prove_from_compiled(
    rep: ProveCReport,
    compiled,
    *,
    cfg: Config,
    repo_root: Path,
    prop: str,
    secret: str | None,
) -> ProveCReport:
    """Shared body for both intakes: lift -> classify -> scaffold -> prove, mapping the orchestrator
    outcome onto the report stages. `compiled` is a CompileResult (from a C compile or a disassemble).
    Every ceiling class is attempted (not short-circuited); a known limitation is labelled as an
    expected failure with the residual goal, rather than presented as a crash."""
    # --- lift stage ---
    lr = intake.lift(compiled, repo_root, prop=prop)
    if not lr.ok:
        rep.stage("lift", Status.FAILED, lr.error or "lift failed")
        rep.error = lr.error
        return rep
    rep.stage("lift", Status.OK,
              f"entry=0x{lr.entry_addr:x} exits={[hex(a) for a in lr.exit_addrs]}")
    rep.lift_log = lr.cfg_description
    rep.ceiling_class = lr.ceiling.value

    # --- classify stage (informational; we attempt every class) ---
    # In scope = a deterministic CFG-derived invariant exists (straight-line OR counter loop).
    expected_fail = lr.invariant is None
    if expected_fail:
        why = CEILING_HELP.get(lr.ceiling, "outside the engine's current reach")
        rep.stage("classify", Status.LIMITATION,
                  f"{lr.ceiling.value} (expected failure: {why}); attempting anyway")
    else:
        scope = "counter loop, derived invariant" if lr.ceiling.value == "counter-loop" else "in scope"
        rep.stage("classify", Status.OK, f"{lr.ceiling.value} ({scope})")
    rep.predicted_cycles = (lr.postcondition or "").replace("cycle_count_of_trace t' ", "") or None
    rep.predicted_range = neorv32_cycle_range(lr.postcondition)

    # --- write + compile the scaffolding so the theorem can be stated ---
    secret = secret if prop == "ct" else None
    spec = intake.build_targetspec(lr, secret_param=secret)
    workspace = Path(cfg.petanque.workspace)
    targets_dir = workspace / "targets"
    targets_dir.mkdir(parents=True, exist_ok=True)
    scaffold_path = targets_dir / f"{lr.scaffold_module}.v"
    scaffold_path.write_text(lr.scaffold_source)
    ok, serr = intake.compile_scaffold(scaffold_path, workspace)
    if not ok:
        rep.stage("lift", Status.FAILED, "scaffolding did not compile")
        rep.error = serr.strip() or "coqc failed on generated scaffolding"
        return rep

    # --- prove stage (orchestrator) ---
    from .agent.orchestrator import Orchestrator
    from .proof.petanque_driver import driver as pet_driver
    from eval.targets import load_proof_library

    needs_model = lr.invariant is None  # only straight-line has a deterministic invariant
    if needs_model:
        from .models import LLM
        try:
            LLM(cfg.model).healthcheck()
        except RuntimeError as e:
            rep.stage("invariant", Status.FAILED, f"model server unreachable: {e}")
            rep.error = str(e)
            return rep

    proof_library = load_proof_library(cfg.eval.targets_file)
    try:
        orch = Orchestrator(cfg)
        with pet_driver(cfg.petanque, default_timeout_s=cfg.agent.tactic_timeout_s) as d:
            res = orch.prove(d, spec, cfg_description=lr.cfg_description,
                             secret_param=secret, gold_invariant=lr.invariant,
                             gold_proof=lr.proof_script, proof_library=proof_library)
    except Exception as e:  # pet-server / connection failures -> clean stage record, not a traceback
        rep.stage("invariant", Status.FAILED, f"prover unreachable: {e}")
        rep.error = str(e)
        return rep

    # --- map the orchestrator outcome onto spec-lint | invariant | repair | stored ---
    rep.proved = res.proved
    rep.attempts, rep.iterations, rep.llm_calls = res.invariant_attempt, res.iterations, res.llm_calls
    rep.residual_goal = res.residual_goal
    rep.added_to_corpus = res.stored_to_corpus
    where = res.residual_goal or res.error or "no residual goal reported"
    fail_status = Status.LIMITATION if expected_fail else Status.FAILED

    if res.error and "spec rejected" in res.error:
        rep.stage("spec-lint", Status.FAILED, res.error)
        rep.error = res.error
        return rep
    rep.stage("spec-lint", Status.OK, "non-vacuous (cycle_count constrained)")

    repaired = res.llm_calls > 0
    if res.proved:
        rep.stage("invariant", Status.OK, "invariant type-checks; theorem stated")
        rep.stage("repair", Status.OK if repaired else Status.SKIPPED,
                  f"closed via repair ({res.closing_tactic})" if repaired
                  else f"closed via {res.closing_tactic}")
        rep.stage("stored", Status.OK if res.stored_to_corpus else Status.FAILED,
                  "added to corpus" if res.stored_to_corpus else "store-back failed (see logs)")
    elif res.residual_goal is not None:
        rep.stage("invariant", Status.OK, "invariant type-checks; theorem stated")
        rep.stage("repair", fail_status,
                  f"proof search stalled at the residual goal; iters={res.iterations} "
                  f"llm={res.llm_calls}")
        rep.stage("stored", Status.SKIPPED, "nothing to store (not proved)")
    else:
        rep.stage("invariant", fail_status, f"no closable invariant within budget: {where}")
        rep.stage("repair", Status.SKIPPED, "not reached")
        rep.stage("stored", Status.SKIPPED, "nothing to store (not proved)")

    if not res.proved:
        rep.error = (f"expected failure for {lr.ceiling.value} "
                     f"({CEILING_HELP.get(lr.ceiling, 'ceiling case')}); proof stalled at: {where}"
                     if expected_fail else where)
    return rep
