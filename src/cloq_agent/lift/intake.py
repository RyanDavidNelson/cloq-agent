"""Turn a compiled object into a Picinæ/Cloq program + a TargetSpec the orchestrator can prove.

This is the `lift` stage of the C-intake path. It:
  1. runs the vendored `riscv_lifter.sh` (objdump -> a `binary : addr -> N` map + start/end);
  2. parses that flat map and the disassembly into `lift/cfg.py` (blocks, loops, timing);
  3. wraps the map in the Cloq `TimingProof` functor scaffolding the theorem builder expects
     (a `Program_<func> <: ProgramInformation` + the RISCVTiming/Automation instantiation),
     mirroring the vendored examples exactly so a generated theorem can be *stated*;
  4. classifies the function against the known proof-engine ceiling (CLAUDE.md): straight-line and
     pure counter loops are in-scope; array/pointer, search-early-exit, and aliasing loops are the
     documented limitations and are reported as such rather than attempted blindly;
  5. builds a `TargetSpec` (CFG-derived pinned postcondition for the straight-line case).

The soundness boundary is unchanged: the scaffolding and the postcondition are derived from the
compiled bytes and the CFG, never from the model; the model still only fills invariant holes.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .cfg import CFG, build_cfg, parse_objdump
from .compile import OBJDUMP, CompileResult
from ..proof.theorem_builder import PRELUDE_LINES, TargetSpec

# Memory-access mnemonics: their presence inside a loop is what separates a pure counter loop
# (in-scope) from an array/pointer loop (a ceiling case needing an exists-index invariant).
_MEM_OPS = {"lw", "lh", "lhu", "lb", "lbu", "sw", "sh", "sb"}
_STORE_OPS = {"sw", "sh", "sb"}
_LIFTER = "riscv_lifter.sh"


class Ceiling(Enum):
    """Where a function lands relative to the proof engine's current reach (CLAUDE.md ceiling)."""
    STRAIGHT_LINE = "straight-line"            # provable end-to-end (WCET closed form from the CFG)
    COUNTER_LOOP = "counter-loop"              # provable in principle; needs a pinned closed form
    ARRAY_POINTER = "array/pointer loop"       # ceiling: exists-index invariant + witness
    SEARCH_EARLY_EXIT = "search early-exit"    # ceiling: data-dependent decidability case-split
    ALIASING = "memory-aliasing branch"        # ceiling: noverlaps / getmem_noverlap reasoning
    UNSUPPORTED = "unsupported control flow"   # nested/irreducible loops, etc.

    @property
    def provable(self) -> bool:
        """True only for what the engine closes today end-to-end without bespoke ITP."""
        return self is Ceiling.STRAIGHT_LINE

    @property
    def is_ceiling(self) -> bool:
        return self in (
            Ceiling.ARRAY_POINTER, Ceiling.SEARCH_EARLY_EXIT,
            Ceiling.ALIASING, Ceiling.UNSUPPORTED,
        )


@dataclass
class LiftResult:
    ok: bool
    func: str
    ceiling: Ceiling | None = None
    cfg: CFG | None = None
    cfg_description: str = ""
    scaffold_source: str | None = None      # the generated Cloq program-scaffolding .v text
    scaffold_module: str | None = None       # its Require-able module name
    entry_addr: int | None = None
    exit_addrs: list[int] = field(default_factory=list)
    postcondition: str | None = None         # CFG-derived pinned WCET claim (straight-line)
    # For the straight-line AND counter-loop cases the whole invariant + a deterministic discharge
    # are CFG-derivable (no LLM): the orchestrator runs them via its gold path. None otherwise.
    invariant: str | None = None
    proof_script: list[str] | None = None
    params: list[tuple[str, str, str]] = field(default_factory=list)   # derived binders (counter loop)
    error: str | None = None


_SYM = re.compile(r"^[0-9a-fA-F]+\s+<([^>]+)>:")


def _slice_function(objdump: str, func: str) -> str:
    """Return the disassembly lines belonging to `<func>`.

    gcc -O2 splits a function across compiler-local label headers (`<.L3>:`, blank-line separated),
    so we must keep capturing through `.L*` / `$*` (mapping-symbol) headers and stop only at the
    next *real* function symbol. A naive "stop at the first blank line" drops the loop body and
    makes every looping function look straight-line — exactly the bug this guards against.
    """
    out: list[str] = []
    capturing = False
    for line in objdump.splitlines():
        m = _SYM.match(line)
        if m:
            name = m.group(1)
            if name == func:
                capturing = True
            elif name.startswith((".L", "$", ".")):
                pass  # a local label of the current function — keep going
            elif capturing:
                break  # a different top-level function — stop
            continue
        if capturing and line.strip():
            out.append(line)
    return "\n".join(out)


def _run_lifter(repo_root: Path, obj_path: Path, def_name: str, objdump: str = OBJDUMP) -> str:
    """Run the vendored riscv_lifter.sh on the object; returns its generated .v text."""
    script = repo_root / "vendor" / "picinae" / _LIFTER
    if not script.exists():
        raise FileNotFoundError(f"lifter not found: {script}")
    env = {**os.environ, "OBJDUMP": objdump}
    r = subprocess.run(
        ["bash", str(script), str(obj_path), def_name],
        capture_output=True, text=True, env=env, timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(f"riscv_lifter.sh failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


_ARM = re.compile(r"\|\s*0x([0-9a-fA-F]+)\s*=>\s*0x([0-9a-fA-F]+)\s*(\(\*.*?\*\))?")


def _parse_lifter_arms(lifted: str) -> list[tuple[int, str, str]]:
    """Extract (addr, opcode_hex, comment) arms from the lifter's flat `binary` definition."""
    arms: list[tuple[int, str, str]] = []
    for m in _ARM.finditer(lifted):
        arms.append((int(m.group(1), 16), m.group(2), (m.group(3) or "").strip()))
    return arms


def _exits_match(exit_addrs: list[int]) -> str:
    cases = "\n        ".join(f"| 0x{a:x} => true" for a in sorted(exit_addrs))
    return (
        "match t with (Addr a, _) :: _ => match a with\n        "
        f"{cases}\n        | _ => false\n        end | _ => false end"
    )


def generate_scaffold(func: str, arms: list[tuple[int, str, str]],
                      entry_addr: int, exit_addrs: list[int]) -> tuple[str, str]:
    """Render the Cloq program scaffolding for a lifted function.

    Returns (module_name, source). The module mirrors the vendored examples (e.g.
    riscv_addloop_timing_proof.v): a `TimingProof (cpu)` functor holding `Program_<func>`
    (entry_addr/exits/binary), the RISCVTiming + RISCVTimingAutomation instantiation, so the
    theorem builder's default module names (TimingProof / RISCVTiming / Program_<func> / <func>Auto)
    resolve and `lifted_prog` / `exits` are in scope at the theorem.
    """
    module = f"{func}_lifted"
    arm_lines = "\n        ".join(
        f"| 0x{a:x} => 0x{op} {c}".rstrip() for a, op, c in sorted(arms)
    )
    src = f"""\
(* AUTO-GENERATED by cloq-agent.lift.intake from compiled C. Do not edit by hand. *)
Require Import RISCVTiming.
Import RISCVNotations.

Module TimingProof (cpu : RVCPUTimingBehavior).

  Module Program_{func} <: ProgramInformation.
    Definition entry_addr : N := 0x{entry_addr:x}.

    Definition exits (t:trace) : bool :=
      {_exits_match(exit_addrs)}.

    Definition binary (a : addr) : N :=
      match a with
        {arm_lines}
        | _ => 0
      end.
  End Program_{func}.

  Module RISCVTiming := RISCVTiming cpu Program_{func}.
  Module {func}Auto := RISCVTimingAutomation RISCVTiming.
  Import Program_{func} {func}Auto.

End TimingProof.
"""
    return module, src


# The straight-line discharge: after the deterministic Picinæ prelude (which runs the base case
# and fans the inductive goal with destruct_inv), step through the single path and discharge the
# pinned cycle equation. Validated to reach Qed on a compiled straight-line function in the rocq
# image. This is generic over straight-line bodies — no loop, so no per-target tuning.
STRAIGHTLINE_CLOSER = "repeat (tstep r5_step); repeat split; try assumption; try lia; try hammer."


def straightline_invariant(func: str, cfg: CFG, postcondition: str) -> str:
    """The complete (hole-free) timing invariant for a straight-line function: cycle count 0 at the
    entry, the pinned closed form at the exit(s). Fully CFG-derived — the model supplies nothing."""
    exits = set(cfg.exit_points())
    arms = [(cfg.entry, "cycle_count_of_trace t' = 0")] if cfg.entry not in exits else []
    arms += [(a, postcondition) for a in sorted(exits)]
    arm_lines = "\n".join(f"| 0x{a:x} => Some ({body})" for a, body in sorted(arms))
    return (
        f"Definition {func}_timing_invs (t:trace) :=\n"
        f"match t with (Addr a, s) :: t' => match a with\n"
        f"{arm_lines}\n| _ => None\nend | _ => None end."
    )


def _imm(tok: str) -> int | None:
    try:
        return int(tok, 0)
    except ValueError:
        return None


def counter_loop_invariant(func: str, cfg: CFG) -> tuple[str, list[tuple[str, str, str]], str] | None:
    """Fix #1 — derive a *counter-loop* invariant from the CFG (no LLM). Returns
    (invariant, params, postcondition), or None if the pattern doesn't match.

    Matches the addloop class: one loop whose induction register is changed by a constant step each
    iteration (an `addi` immediate, or `sub`/`add` of a constant helper register set in the entry
    prefix). The whole invariant is mechanical: the loop-invariant constants come from the prefix's
    `ori/andi/addi` immediates, the iteration count from the IV's distance to its entry value (which
    we bind as a fresh universal `n` tied to the IV register), and the cycle closed form from the
    CFG loop timing. Self-contained — no source signature / params needed.
    """
    from .cfg import _reg

    if len(cfg.loop_headers) != 1:
        return None
    h = cfg.loop_headers[0]
    tim = cfg.loop_timing(h)
    if tim is None:
        return None
    prefix, body = tim

    # constant helper registers from the entry->header prefix (ori t2,1 / andi t3,t3,0 / li / addi)
    consts: dict[str, int] = {}
    for ins in sorted((i for b in cfg.blocks.values() for i in b.insns), key=lambda i: i.addr):
        if not (cfg.entry <= ins.addr < h):
            continue
        ops = [o.strip() for o in ins.operands.split(",")]
        if ins.mnemonic in ("ori", "addi", "li") and len(ops) >= 3 and ops[1] in ("zero", "x0") \
                and _imm(ops[2]) is not None:
            consts[_reg(ops[0])] = _imm(ops[2])
        elif ins.mnemonic == "andi" and len(ops) == 3 and ops[2] in ("0", "0x0"):
            consts[_reg(ops[0])] = 0

    # induction register: changed by a constant step each iteration
    iv = None  # (reg, step)  step<0 for a decrement
    for ins in sorted((i for s in cfg.natural_loop(h) for i in cfg.blocks[s].insns), key=lambda i: i.addr):
        ops = [o.strip() for o in ins.operands.split(",")]
        if ins.mnemonic == "addi" and len(ops) == 3 and ops[0] == ops[1] and _imm(ops[2]) is not None:
            iv = (_reg(ops[0]), _imm(ops[2]))
        elif ins.mnemonic in ("sub", "add") and len(ops) == 3 and ops[0] == ops[1] \
                and _reg(ops[2]) in consts:
            iv = (_reg(ops[0]), (-1 if ins.mnemonic == "sub" else 1) * consts[_reg(ops[2])])
    if iv is None or iv[1] == 0:
        return None
    iv_reg, step = iv

    # bind the IV's entry value as a fresh universal `n` (the iteration count)
    n = "n"
    count = f"({n} - s {iv_reg})" if step < 0 else f"(s {iv_reg} - {n})"
    bound = f"s {iv_reg} <= {n}" if step < 0 else f"{n} <= s {iv_reg}"
    body_e = f"({body})"
    exit_term = _loop_exit_term(cfg, h)
    const_facts = "".join(f"s {r} = {v} /\\ " for r, v in sorted(consts.items()))

    exits = cfg.exit_points()
    loop_arm = f"{const_facts}{bound} /\\ cycle_count_of_trace t' = {prefix} + {count} * {body_e}"
    post = f"cycle_count_of_trace t' = {prefix} + {n} * {body_e}{exit_term}"
    arms = [(cfg.entry, f"s {iv_reg} = {n} /\\ cycle_count_of_trace t' = 0"), (h, loop_arm)]
    arms += [(a, post) for a in exits if a != h]
    arm_lines = "\n".join(f"| 0x{a:x} => Some ({b})" for a, b in sorted(arms))
    invariant = (f"Definition {func}_timing_invs ({n} : N) (t:trace) :=\n"
                 f"match t with (Addr a, s) :: t' => match a with\n{arm_lines}\n"
                 f"| _ => None\nend | _ => None end.")
    return invariant, [(n, "N", iv_reg)], post


def _loop_exit_term(cfg: CFG, header: int) -> str:
    """The `+ t<branch>` term for the loop's exit (the header branch, taken on exit)."""
    blk = cfg.blocks.get(header)
    if blk and blk.insns:
        last = blk.insns[-1]
        if last.mnemonic in ("beq", "bne", "blt", "bge", "bltu", "bgeu"):
            return f" + tt{last.mnemonic}"
    return ""


def straightline_proof(addr_width: int = 32) -> list[str]:
    """The deterministic discharge script for the gold path: the Picinæ prelude then the closer."""
    return [ln.format(width=addr_width) for ln in PRELUDE_LINES] + [STRAIGHTLINE_CLOSER]


# The reusable loop-discharge tactic (fix #2). Per `destruct_inv` obligation it: destructs the
# invariant's `/\` chain + `exists i`; steps the block (`tstep r5_step`); instantiates the next
# witness (`eexists`); applies the modular-arithmetic rewrites the timing goals need
# (`msub_nowrap` for `a ⊖ b`, `N_sub_distr` for `(a-b)*c`); then closes with split/assumption/
# psimpl/lia/hammer. Verified to close the counter-loop class (addloop) given a correct invariant.
SOLVE_TIMING_LOOP = (
    "Local Ltac solve_timing_loop := "
    "repeat (match goal with "
    "| [ H : _ /\\ _ |- _ ] => destruct H "
    "| [ H : exists _, _ |- _ ] => destruct H end); "
    "repeat (tstep r5_step); "
    "repeat (match goal with |- exists _, _ => eexists end); "
    "try (rewrite msub_nowrap by (psimpl; lia)); try (rewrite N_sub_distr by lia); "
    "repeat split; try assumption; try (psimpl; lia); try lia; try hammer; "
    "try (rewrite N_sub_distr; lia)."
)

# The loop discharge script: define the tactic, run the base case (entry arm: register facts +
# cycle=0), set up the inductive case, `destruct_inv`, then `all: solve_timing_loop`.
_LOOP_PRELUDE = (
    "intros.",
    "apply prove_invs.",
    "simpl. rewrite ENTRY. unfold entry_addr. repeat (tstep r5_step). "
    "repeat split; try assumption; try (psimpl; lia); try lia; try hammer.",
    "intros.",
    "eapply startof_prefix in ENTRY; try eassumption.",
    "eapply preservation_exec_prog in MDL; try eassumption; [idtac|apply lift_riscv_welltyped].",
    "clear - ENTRY PRE MDL. rename t1 into t. rename s1 into s'.",
    "destruct_inv {width} PRE.",
    "all: solve_timing_loop.",
)


def loop_proof(addr_width: int = 32) -> list[str]:
    """Deterministic discharge for a counter-loop-class proof: the `solve_timing_loop` tactic, then
    the loop prelude applying it to every `destruct_inv` obligation. Verified to close `addloop`
    (the pure counter loop) with no LLM, given a correct invariant — the bottom rung of the loop
    curriculum. Array/stride loops add witness + memory reasoning the tactic does not yet cover."""
    return [SOLVE_TIMING_LOOP] + [ln.format(width=addr_width) for ln in _LOOP_PRELUDE]


def classify(cfg: CFG) -> Ceiling:
    """Classify a function against the proof-engine ceiling, purely from the CFG."""
    headers = cfg.loop_headers
    if not headers:
        return Ceiling.STRAIGHT_LINE

    # Multiple loop headers or a loop that is its own irreducible tangle: out of scope for now.
    worst = Ceiling.COUNTER_LOOP
    for h in headers:
        loop = cfg.natural_loop(h)
        insns = [i for s in loop for i in cfg.blocks[s].insns]
        mnems = {i.mnemonic for i in insns}
        n_exits = len({dst for _, dst in cfg.loop_exit_edges(h)})
        has_mem = bool(mnems & _MEM_OPS)
        has_store = bool(mnems & _STORE_OPS)

        if n_exits > 1 and has_mem:
            cls = Ceiling.SEARCH_EARLY_EXIT          # data-dependent early break over memory
        elif has_store and n_exits > 1:
            cls = Ceiling.ALIASING                    # store under a data-dependent branch
        elif has_mem:
            cls = Ceiling.ARRAY_POINTER               # array/pointer walk
        elif n_exits > 1:
            cls = Ceiling.SEARCH_EARLY_EXIT           # data-dependent exit, no memory
        else:
            cls = Ceiling.COUNTER_LOOP                # pure counter loop
        worst = _worse(worst, cls)
    if len(headers) > 1:
        worst = _worse(worst, Ceiling.UNSUPPORTED)
    return worst


# Order from most-in-scope to least, so `_worse` reports the hardest class a function hits.
_SEVERITY = [
    Ceiling.STRAIGHT_LINE, Ceiling.COUNTER_LOOP, Ceiling.ARRAY_POINTER,
    Ceiling.SEARCH_EARLY_EXIT, Ceiling.ALIASING, Ceiling.UNSUPPORTED,
]


def _worse(a: Ceiling, b: Ceiling) -> Ceiling:
    return a if _SEVERITY.index(a) >= _SEVERITY.index(b) else b


def lift(compiled: CompileResult, repo_root: Path, prop: str = "wcet") -> LiftResult:
    """Lift a successful CompileResult into a CFG, scaffolding, classification, and postcondition.

    `prop` chooses the straight-line claim shape (fix #1, path-aware bound): WCET asserts a SOUND
    upper bound `cycle_count_of_trace t' <= sum` (the flat instruction sum bounds any single path,
    so it stays provable even when a forward branch makes the exact count path-dependent); CT keeps
    the exact `= sum` (constant-time means the *same* count on every path).
    """
    func = compiled.func
    if not compiled.ok or compiled.objdump is None or compiled.obj_path is None:
        return LiftResult(False, func, error="lift called on a failed/empty compile result")

    sliced = _slice_function(compiled.objdump, func) or compiled.objdump
    cfg = build_cfg(parse_objdump(sliced))
    if not cfg.blocks:
        return LiftResult(False, func, error="no instructions parsed from the disassembly")

    ceiling = classify(cfg)
    entry_addr = cfg.entry
    exit_addrs = cfg.exit_points() or [max(cfg.blocks)]

    try:
        lifted = _run_lifter(repo_root, compiled.obj_path, f"{func}_bin")
        arms = _parse_lifter_arms(lifted)
    except (OSError, RuntimeError) as e:
        return LiftResult(False, func, ceiling=ceiling, cfg=cfg,
                          cfg_description=cfg.describe(), error=f"lifter: {e}")
    if not arms:
        return LiftResult(False, func, ceiling=ceiling, cfg=cfg,
                          cfg_description=cfg.describe(), error="lifter produced no instructions")

    module, scaffold = generate_scaffold(func, arms, entry_addr, exit_addrs)

    # Pinned WCET postcondition: only the straight-line case has a CFG-derivable exact closed form
    # today. Counter loops are provable in principle but need a pinned loop closed form (the
    # research track), so we leave postcondition None and let the diagnostic say so.
    postcondition = invariant = None
    proof_script = None
    params: list[tuple[str, str, str]] = []
    if ceiling is Ceiling.STRAIGHT_LINE:
        total = cfg.straightline_cycles()
        rel = "=" if prop == "ct" else "<="     # CT: exact (same on every path); WCET: upper bound
        postcondition = f"cycle_count_of_trace t' {rel} {total or '0'}"
        invariant = straightline_invariant(func, cfg, postcondition)
        proof_script = straightline_proof()
    elif ceiling is Ceiling.COUNTER_LOOP:
        # Fix #1+#2: derive the counter-loop invariant + discharge it with solve_timing_loop (no LLM).
        derived = counter_loop_invariant(func, cfg)
        if derived is not None:
            invariant, params, postcondition = derived
            proof_script = loop_proof()

    return LiftResult(
        ok=True, func=func, ceiling=ceiling, cfg=cfg, cfg_description=cfg.describe(),
        scaffold_source=scaffold, scaffold_module=module,
        entry_addr=entry_addr, exit_addrs=exit_addrs, postcondition=postcondition,
        invariant=invariant, proof_script=proof_script, params=params,
    )


def _coqproject_args(proofs_dir: Path) -> list[str]:
    """The load-path / warning args from proofs/_CoqProject, flattened for a direct `coqc` call."""
    args: list[str] = []
    cp = proofs_dir / "_CoqProject"
    if not cp.exists():
        return args
    for line in cp.read_text().splitlines():
        line = line.strip()
        if line.startswith(("-R", "-I", "-Q", "-arg")):
            args.extend(t for t in line.split() if t != "-arg")
    return args


def compile_scaffold(scaffold_path: Path, proofs_dir: Path, coqc: str | None = None) -> tuple[bool, str]:
    """`coqc` the generated scaffolding to a .vo on the proofs load path so the orchestrator's
    theorem can `Require Import` it. Returns (ok, stderr). Needs a Rocq toolchain with the
    vendored Picinæ prebuilt — i.e. run inside / against the rocq image (set CLOQ_COQC to override).
    """
    coqc = coqc or os.environ.get("CLOQ_COQC", "coqc")
    if not shutil.which(coqc):
        return False, (f"coqc '{coqc}' not on PATH — compile the scaffolding inside the rocq image "
                       f"(or set CLOQ_COQC)")
    args = [coqc, *_coqproject_args(proofs_dir), str(scaffold_path)]
    r = subprocess.run(args, capture_output=True, text=True, cwd=proofs_dir, timeout=300)
    return r.returncode == 0, r.stderr


def build_targetspec(lift_res: LiftResult, *, theorem_name: str | None = None,
                     params: list[tuple[str, ...]] | None = None,
                     secret_param: str | None = None) -> TargetSpec:
    """Build the TargetSpec for a lifted function. Module names follow the generated scaffolding."""
    func = lift_res.func
    return TargetSpec(
        name=func,
        # RISCVTiming re-exports the RVCPUTimingBehavior module type (the functor parameter) and
        # NEORV32 provides the concrete CPU for the final instantiation; the scaffold provides
        # TimingProof. Order matters: dependencies before the scaffold that uses them.
        requires=["NEORV32", "RISCVTiming", lift_res.scaffold_module],
        lifted_program="lifted_prog",
        entry_addr=lift_res.entry_addr or 0,
        exit_point="exits",
        theorem_name=theorem_name or f"{func}_timing_gen",
        params=params if params is not None else (lift_res.params or []),
        program_module=f"Program_{func}",
        auto_module=f"{func}Auto",
        postcondition=lift_res.postcondition,
    )
