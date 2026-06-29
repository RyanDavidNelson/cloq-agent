"""Assemble a Rocq source file stating a Cloq timing theorem.

Cloq proofs are functor-structured: instantiate the CPU (e.g. NEORV32) and the program's
RISCVTiming/Automation modules, THEN state `satisfies_all lifted_prog (inv ...) exits t`.
A flat `Require Import Picinae_riscv` is not enough — N, cycle_count_of_trace, the register
names, the t* timing constants, lifted_prog and exits all live inside the instantiated modules.

Everything program-specific is driven from the `TargetSpec` so a second, distinct program
renders without editing this file:

  * the `Require Import` lines come from `spec.requires`;
  * the program / automation / timing submodule names come from spec fields;
  * the in-scope `lifted_prog` / `exits` names come from `spec.lifted_program`/`spec.exit_point`;
  * the entry hypotheses come from each param's bound register (`(name, type, reg)`).

Every new field defaults to addloop's value, so the existing smoke target is unchanged. The
soundness boundary is preserved: the model fills the `{invariant}` slot only; the postcondition
(`satisfies_all ...`) is pinned here from the trusted spec, and the entry/exit addresses come
from the program module (the CFG), never the model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# addloop's calling convention, used only when a spec's params carry no register bindings.
_DEFAULT_ENTRY_REGS: tuple[str, ...] = ("R_T0", "R_T1")


@dataclass
class TargetSpec:
    name: str
    requires: list[str]
    lifted_program: str
    entry_addr: int
    exit_point: str
    theorem_name: str
    params: list[tuple[str, ...]]
    # Functor / module names. Defaults mirror the vendored addloop proof for back-compat;
    # a distinct program overrides these (see eval/targets.yaml for the field documentation).
    timing_functor: str = "TimingProof"      # Module Inner := <functor> cpu.
    timing_submodule: str = "RISCVTiming"     # Import Inner.<submodule>.
    program_module: str = "Program_addloop"   # Import Inner.<program_module>.
    auto_module: str = "addloopAuto"          # Import Inner.<auto_module>.
    cpu_module: str = "NEORV32"               # concrete CPU functor for the final instantiation
    cpu_config: str = "NEORV32BaseConfig"     # config module fed to the CPU functor
    # Address width passed to `destruct_inv {addr_width} PRE` in the prelude (32 for RV32).
    addr_width: int = 32
    # Extra entry-precondition hypotheses beyond the per-param register ties: (name, prop) pairs
    # emitted verbatim as `(name: prop)` after the register hypotheses. These are well-formedness
    # assumptions on the inputs (e.g. pointer alignment, length-in-bounds) that the program needs
    # but that are NOT a single register = value. They come from the TRUSTED spec, never the model,
    # so the soundness boundary (model fills invariant arms only) is preserved — they constrain the
    # theorem's inputs, they cannot widen or weaken the pinned postcondition.
    entry_hyps: list[tuple[str, str]] = field(default_factory=list)
    # Extra universally-quantified binders that are NOT invariant arguments: (name, type, reg)
    # triples. Each adds a `forall ... (name : type)` binder and a register entry hypothesis
    # `(Reg: s reg = name)`, but is NOT passed to the invariant. Use this for call-ABI argument
    # registers the timing invariant doesn't parameterize by (e.g. vListInsertEnd takes a second
    # pointer in a1 that its vendored invariant ignores). They constrain inputs only, so the
    # soundness boundary holds. inv_args stays driven solely by `params`.
    extra_binders: list[tuple[str, str, str]] = field(default_factory=list)
    # Explicit argument list applied to the invariant in the goal `(inv_name <inv_args>)`. Defaults
    # to the param names. Override when the (vendored) invariant takes an argument that is not a
    # param binder — notably the universally-quantified store `s` as a leading arg (some vendored
    # invariants declare a shadowed `(s : store)` first parameter), e.g. ["s", "arr", "key", "len"].
    inv_args: list[str] | None = None
    # The pinned exit-arm proposition for skeleton synthesis (the trusted WCET/ct claim). The
    # model never supplies this; it is spliced in verbatim. None disables skeleton synthesis.
    postcondition: str | None = None
    # Coq definitions emitted INTO the functor (after the module imports, before the invariant):
    # the Phase-2 array-search decidability template (`key_in_array`/`key_in_array_dec`/the timing
    # disjunction), specialised to the recovered array shape. The proof's case-split then runs on
    # these emitted defs instead of a hand-written per-program copy. None for non-search targets.
    search_defs: str | None = None


# NArith / Picinae_riscv are framework foundations (not program-specific), so they stay a
# constant prelude; the program/CPU/timing requires are appended from spec.requires.
#
# The file is split at `Proof.`: the PREFIX (functor header + invariant + theorem statement)
# is always emitted; the SUFFIX (functor `End` + concrete-CPU instantiation) is emitted ONLY
# when the caller supplies a closed proof script. An OPEN proof (proof_body=None) emits the
# deterministic prelude after `Proof.` and stops there — an unfinished proof cannot be followed
# by `End {thm}_Proof.`, and the search drives it to Qed interactively through petanque.
_FUNCTOR_PREFIX = """\
(* AUTO-GENERATED by cloq-agent.theorem_builder. Do not edit by hand. *)
Require Import NArith.
Require Import Picinae_riscv.
{requires}
Import RISCVNotations.

(* Reuse the vendored program ({program_module}) by instantiating its functor;
   state the agent's theorem in the SAME scope so Picinae primitives (startof,
   models, rvtypctx) and functor members ({lifted_prog}, {exits}) are all visible. *)
Module {thm}_Proof (cpu : RVCPUTimingBehavior).

  Module Inner := {timing_functor} cpu.
  Import Inner.
  (* Import Inner alone only exposes Inner's *direct* members; the Picinae
     primitives (startof/models/rvtypctx) and the functor outputs
     ({lifted_prog}/{exits}/entry_addr/cycle_count_of_trace + the t* timing
     constants) live in these instantiated submodules and are otherwise
     invisible at the theorem's scope. *)
  Import Inner.{timing_submodule}.
  Import Inner.{program_module}.
  Import Inner.{auto_module}.
{search_defs}
{invariant}

  Theorem {thm} :
    forall s t s' x' {binders}
      (ENTRY: startof t (x',s') = (Addr entry_addr, s))
      (MDL: models rvtypctx s){entry_hyps},
    satisfies_all {lifted_prog} ({inv_name} {inv_args}) {exits} ((x',s') :: t).
  Proof.
"""

_FUNCTOR_SUFFIX = """\

End {thm}_Proof.

Module {thm}_CPU := {cpu_module} {cpu_config}.
Module {thm}_Concrete := {thm}_Proof {thm}_CPU.
"""

# T1 structural prelude (CLAUDE.md "Picinae tactic vocabulary"): identical every proof,
# deterministic, NOT LLM-discovered. `step` is rebound per proof (it is undefined otherwise),
# then the Picinae setup runs the base case, sets up the inductive step, and `destruct_inv`
# fans the inductive goal into one subgoal per invariant program point. Mirrors the vendored
# list proofs (vListInitialise.v / uxListRemove.v). {width} = address width (32 for RV32).
PRELUDE_LINES: tuple[str, ...] = (
    "Local Ltac step := tstep r5_step.",
    "intros.",
    "apply prove_invs.",
    "simpl. rewrite ENTRY. unfold entry_addr. now step.",
    "intros.",
    "eapply startof_prefix in ENTRY; try eassumption.",
    "eapply preservation_exec_prog in MDL; try eassumption; [idtac|apply lift_riscv_welltyped].",
    "clear - ENTRY PRE MDL. rename t1 into t. rename s1 into s'.",
    "destruct_inv {width} PRE.",
)


def prelude(addr_width: int = 32) -> str:
    """The deterministic prelude as a `\\n    `-indented block, ready to splice after `Proof.`."""
    return "\n    ".join(line.format(width=addr_width) for line in PRELUDE_LINES)


def _binders(params: list[tuple[str, ...]]) -> str:
    if not params:
        return ""
    return " ".join(f"({p[0]} : {p[1]})" for p in params)


def _inv_args(params: list[tuple[str, ...]]) -> str:
    return " ".join(p[0] for p in params)


def _hyp_name(reg: str) -> str:
    """Name the entry hypothesis after the register it ties (R_T0 -> T0), as the vendored proof does."""
    return reg[2:] if reg.startswith("R_") else reg


def _entry_bindings(params: list[tuple[str, ...]]) -> list[tuple[str, str, str]]:
    """(hyp_name, reg, param_name) per entry hypothesis.

    A param may carry the register it binds as a 3rd element: ("x", "N", "R_A0"). If *no*
    param carries a register we fall back to addloop's documented convention (the first params
    bind R_T0, R_T1). Anything past that fallback without an explicit register is an error
    rather than a silent guess of the calling convention.
    """
    for p in params:
        if len(p) not in (2, 3):
            raise ValueError(
                f"theorem_builder: malformed param {p!r}; expected (name, type) "
                f"or (name, type, reg)"
            )

    have_regs = any(len(p) >= 3 and p[2] for p in params)
    if have_regs:
        return [(_hyp_name(p[2]), p[2], p[0]) for p in params if len(p) >= 3 and p[2]]

    if len(params) > len(_DEFAULT_ENTRY_REGS):
        raise ValueError(
            f"theorem_builder: target has {len(params)} params but none specify a bound "
            f"register, and the addloop back-compat default only covers {_DEFAULT_ENTRY_REGS}. "
            f"Give each param its register, e.g. params: [[\"x\", \"N\", \"R_A0\"], ...]."
        )
    return [(_hyp_name(reg), reg, p[0]) for p, reg in zip(params, _DEFAULT_ENTRY_REGS)]


def _entry_hyps_block(
    params: list[tuple[str, ...]],
    extra_binders: list[tuple[str, str, str]],
    extra: list[tuple[str, str]],
) -> str:
    bindings = _entry_bindings(params)
    # extra_binders always carry an explicit register (name, type, reg) -> (hyp, reg, name).
    bindings += [(_hyp_name(reg), reg, name) for name, _ty, reg in extra_binders]
    block = "".join(f"\n      ({name}: s {reg} = {pname})" for name, reg, pname in bindings)
    block += "".join(f"\n      ({name}: {prop})" for name, prop in extra)
    return block


def render(
    spec: TargetSpec,
    invariant_def: str,
    invariant_name: str,
    proof_body: str | None = None,
) -> str:
    """Render the timing theorem as a Rocq source file.

    `proof_body=None` (default): leave the proof OPEN — emit the deterministic Picinae prelude
    after `Proof.` and stop, with no Qed and no functor `End`/instantiation. This is the file
    the search drives interactively through petanque (the prelude reaches `destruct_inv`, then
    the discharge step closes the fan-out subgoals).

    `proof_body=<script>`: emit that script (which must close the proof, ending in `Qed.`) and
    then the functor `End` + concrete-CPU instantiation. Used for a fully-determined gold proof.
    """
    requires_block = "\n".join(f"Require Import {r}." for r in spec.requires)
    # Binders = invariant params, then any extra (non-invariant) ABI-register binders.
    binders = " ".join(b for b in (_binders(spec.params), _binders(spec.extra_binders)) if b)
    prefix = _FUNCTOR_PREFIX.format(
        requires=requires_block,
        invariant=invariant_def.strip(),
        thm=spec.theorem_name,
        timing_functor=spec.timing_functor,
        timing_submodule=spec.timing_submodule,
        program_module=spec.program_module,
        auto_module=spec.auto_module,
        binders=binders,
        entry_hyps=_entry_hyps_block(spec.params, spec.extra_binders, spec.entry_hyps),
        lifted_prog=spec.lifted_program,
        exits=spec.exit_point,
        inv_name=invariant_name,
        inv_args=" ".join(spec.inv_args) if spec.inv_args else _inv_args(spec.params),
        search_defs=(f"\n{spec.search_defs}\n" if spec.search_defs else ""),
    )
    if proof_body is None:
        # Open proof for interactive driving: prelude only, no closer.
        return f"{prefix}    {prelude(spec.addr_width)}\n"
    suffix = _FUNCTOR_SUFFIX.format(
        thm=spec.theorem_name, cpu_module=spec.cpu_module, cpu_config=spec.cpu_config,
    )
    return f"{prefix}    {proof_body.strip()}\n{suffix}"


def write(spec: TargetSpec, source: str, workspace: Path) -> Path:
    out = Path(workspace) / "targets" / f"{spec.name.capitalize()}_gen.v"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(source)
    return out
