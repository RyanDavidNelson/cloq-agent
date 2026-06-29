"""Reproducibly assemble the held-out se_find_eq bottom-test search proof scaffold — the staged
checkpoint the closer arms are edited against. Emits two files under proofs/targets/:

  * se_find_eq_lifted.v  — the GENERATED program functor (intake.lift from the compiled C);
  * Se_find_eq_gen.v     — the theorem: emitted cloq_ decidability + the bottom-test time_of
                           (two-level match, len=0 guard folded in) + the disjunction
                           postcondition + the BODY (0x1c) invariant (i = a4, moving ptr
                           s R_A5 = arr (+) 4*i, cycle = pro + i*body_cont), OPEN proof.

LOAD-PATH + PET-SERVER ORDER (load-bearing, invisible from the code — this is the bug that cost an
afternoon): compile the program with the project's OWN load path (`_coqproject_args` -> the
_CoqProject `-R targets Targets`), NOT a manual `-Q targets ''`; the two are incompatible and the
pet-server will report the generated module's members ("lifted_prog"/"entry_addr") as not found
even though coqc compiles clean. THEN restart the pet-server so coq-lsp drops the stale .vo:

    python eval/heldout/build_se_find_eq.py        # regenerates + compiles the .v with the right path
    docker restart docker-rocq-1                    # drop the cached .vo  (RECOMPILE *then* RESTART)
    # now `PetanqueDriver(...).start(".../Se_find_eq_gen.v", "se_find_eq_timing_gen")` elaborates.

Needs the riscv cross-toolchain on PATH (host) for compile_c and a running docker-rocq-1 for coqc.

CLOSER SKELETON (drives to the three fanned goals; arms are the remaining ITP):
    intros.
    destruct (len =? 0) eqn:LEN0.
    - apply N.eqb_eq in LEN0; subst len. apply prove_invs.   (* len=0 guard: base + inductive *)
        <guard base case: step to the taken-guard exit; cycle = guard term>
    - apply N.eqb_neq in LEN0. apply prove_invs.             (* len>=1 search *)
        <base case through the guard-FALLTHROUGH + j to the body (0x1c); item-1 stepping>
        <inductive: startof_prefix / preservation / destruct_inv>
        destruct (cloq_key_in_array_dec (s' V_MEM32) arr key len) as [IN | NOT_IN].
        + <found: handle_ex; exists (s' R_A4); reconcile pro + i*body_cont + found_partial + shut_f>
        + <not-found: reconcile at i = len-1: pro + (len-1)*body_cont + body_exit + shut_nf>
Two duals are trace-pre-validated: not-found exit at i = len-1, found witness a4 = i. If an arm
fights, check the len>=1 BASE case first — it is the only boundary not yet trace-checked, and the
likely failure is control-flow (tstep not traversing the `j` to land at 0x1c), not arithmetic.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "src"))

from cloq_agent.lift import intake                                          # noqa: E402
from cloq_agent.lift.cfg import build_cfg, parse_objdump                    # noqa: E402
from cloq_agent.lift.compile import compile_c                               # noqa: E402
from cloq_agent.lift.search_template import (                               # noqa: E402
    decidability_block, time_of_bottom_test, timing_postcondition_block,
)
from cloq_agent.proof.theorem_builder import render                        # noqa: E402

P = "cloq_"
ROCQ = "docker-rocq-1"


def main() -> None:
    ws = _REPO / "proofs"
    cr = compile_c(_REPO / "eval/heldout/se_find_eq.c", "se_find_eq")
    lr = intake.lift(cr, _REPO, prop="wcet")
    (ws / "targets" / "se_find_eq_lifted.v").write_text(lr.scaffold_source)

    # Compile the program functor with the PROJECT load path (NOT a manual -Q). Run in the rocq
    # container (coqc lives there); the args come from proofs/_CoqProject so the .vo's logical name
    # is `Targets.se_find_eq_lifted`, matching what the pet-server expects.
    args = " ".join(intake._coqproject_args(ws))
    rc = subprocess.run(["docker", "exec", ROCQ, "bash", "-lc",
                         f"cd /work/proofs && coqc {args} targets/se_find_eq_lifted.v"],
                        capture_output=True, text=True)
    print("program compile:", "OK" if rc.returncode == 0 else rc.stderr[-400:])

    cfg = build_cfg(parse_objdump(cr.objdump))
    h = cfg.loop_headers[0]
    shape = cfg.array_search_shape(h)
    bt = cfg.bottom_test_timing(h)

    spec = intake.build_targetspec(lr)
    spec.params = [("arr", "N", "R_A0"), ("key", "N", "R_A1"), ("len", "N", "R_A2"),
                   ("base_mem", "memory")]
    spec.inv_args = ["arr", "key", "len", "base_mem"]
    spec.entry_hyps = [("MEM", "s V_MEM32 = base_mem"), ("LEN_VALID", "4 * len < 2^32"),
                       ("PTR_ALIGN", "exists k', arr = 4 * k'")]
    spec.theorem_name = "se_find_eq_timing_gen"
    spec.search_defs = (decidability_block(shape, P) + "\n\n"
                        + time_of_bottom_test(P + "time_of_se_find_eq", "len", bt) + "\n\n"
                        + timing_postcondition_block(shape, P + "time_of_se_find_eq", P))

    body_inv = ("(exists i, i < len /\\ s R_A4 = i /\\ s R_A5 = arr ⊕ (4 * i) /\\ s R_A1 = key /\\ "
                "s R_A0 = len /\\ s R_A2 = len /\\ s V_MEM32 = base_mem /\\ "
                "(forall j, j < i -> base_mem Ⓓ[arr ⊕ (4 * j)] <> key) /\\ "
                f"cycle_count_of_trace t' = {bt.pro} + i * ({bt.body_cont}))")
    entry_inv = ("(s V_MEM32 = base_mem /\\ s R_A0 = arr /\\ s R_A1 = key /\\ s R_A2 = len /\\ "
                 "(4 * len < 2^32) /\\ (exists k', arr = 4 * k') /\\ cycle_count_of_trace t' = 0)")
    inv = (
        "Definition se_find_eq_timing_invs (arr key len : N) (base_mem : memory) (t:trace) "
        ": option Prop :=\n"
        "match t with (Addr a, s) :: t' => match a with\n"
        f"| 0x0 => Some {entry_inv}\n| 0x1c => Some {body_inv}\n"
        f"| 0x2c => Some ({P}timing_postcondition base_mem arr key len t)\n"
        f"| 0x30 => Some ({P}timing_postcondition base_mem arr key len t)\n"
        "| _ => None end | _ => None end.")

    (ws / "targets" / "Se_find_eq_gen.v").write_text(
        render(spec, inv, "se_find_eq_timing_invs", proof_body=None))
    print("wrote proofs/targets/{se_find_eq_lifted,Se_find_eq_gen}.v  "
          "-> now `docker restart docker-rocq-1`, then drive the closer.")


if __name__ == "__main__":
    main()
