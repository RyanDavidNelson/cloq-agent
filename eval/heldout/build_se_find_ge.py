"""Assemble the held-out se_find_ge (`>=`) bottom-test search scaffold — the predicate sibling of
build_se_find_eq.py. se_find_ge is byte-identical to se_find_eq except the loop-body compare branch
(`bltu elem,key` instead of `bne elem,key`): the loop CONTINUES while `elem < key` and falls through
to the return when `elem >= key`, so the membership predicate is `key <= mem[..]` (decided by
`N.le_dec`), not `mem[..] = key`. The deterministic front (classify -> search early-exit, shape
recovery base=R_A0/stride=4/bound=R_A2/index, premise gate) is IDENTICAL to se_find_eq; only the
predicate and the auto-derived body-compare timing constant (`tfbltu`/`ttbltu` vs `tfbne`/`ttbne`)
change. That is the whole point of this target: it measures that the decidability template + closer
generalise across the comparison, not that they recall a structural twin.

Emits two files under proofs/targets/:
  * se_find_ge_lifted.v  — the GENERATED program functor (intake.lift from the compiled C);
  * Se_find_ge_gen.v     — the theorem: emitted (`cloq_`-namespaced, GE-specialised) decidability +
                           the bottom-test time_of + the GE disjunction postcondition + the BODY
                           (0x1c) invariant, OPEN proof (the closer is in Se_find_ge_gen.v itself).

LOAD-PATH + PET-SERVER ORDER (same gotcha as se_find_eq): compile the program with the project's OWN
load path (`_coqproject_args` -> the _CoqProject `-R targets Targets`), NOT a manual `-Q targets ''`.
THEN restart the pet-server so coq-lsp drops the stale .vo:

    python eval/heldout/build_se_find_ge.py        # regenerates + compiles the .v with the right path
    docker restart docker-rocq-1                    # drop the cached .vo  (RECOMPILE *then* RESTART)
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
    PRED_GE, decidability_block, time_of_bottom_test, timing_postcondition_block,
)
from cloq_agent.proof.theorem_builder import render                        # noqa: E402

P = "cloq_"
ROCQ = "docker-rocq-1"
PRED = PRED_GE   # the only knob vs build_se_find_eq.py: the comparison the search tests


def main() -> None:
    ws = _REPO / "proofs"
    cr = compile_c(_REPO / "eval/heldout/se_find_ge.c", "se_find_ge")
    lr = intake.lift(cr, _REPO, prop="wcet")
    (ws / "targets" / "se_find_ge_lifted.v").write_text(lr.scaffold_source)

    # Compile the program functor with the PROJECT load path (NOT a manual -Q). Run in the rocq
    # container (coqc lives there); the args come from proofs/_CoqProject so the .vo's logical name
    # is `Targets.se_find_ge_lifted`, matching what the pet-server expects.
    args = " ".join(intake._coqproject_args(ws))
    rc = subprocess.run(["docker", "exec", ROCQ, "bash", "-lc",
                         f"cd /work/proofs && coqc {args} targets/se_find_ge_lifted.v"],
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
    spec.theorem_name = "se_find_ge_timing_gen"
    spec.search_defs = (decidability_block(shape, P, PRED) + "\n\n"
                        + time_of_bottom_test(P + "time_of_se_find_ge", "len", bt) + "\n\n"
                        + timing_postcondition_block(shape, P + "time_of_se_find_ge", P, PRED))

    # Identical to se_find_eq EXCEPT the first-match NotFound clause uses the GE negation
    # (`base_mem Ⓓ[..] < key`, i.e. ~ (key <= elem)) instead of `<> key`. The moving-pointer tie
    # keeps ⊕ (the real modular a5); the MEMORY clause uses plain + to match the emitted template;
    # `4 * len < 2^32` must live in the loop invariant for the no-wrap reductions in every arm.
    body_inv = ("(exists i, i < len /\\ 4 * len < 2^32 /\\ s R_A4 = i /\\ s R_A5 = arr ⊕ (4 * i) "
                "/\\ s R_A1 = key /\\ s R_A0 = len /\\ s R_A2 = len /\\ s V_MEM32 = base_mem /\\ "
                "(forall j, j < i -> base_mem Ⓓ[arr + (4 * j)] < key) /\\ "
                f"cycle_count_of_trace t' = {bt.pro} + i * ({bt.body_cont}))")
    entry_inv = ("(s V_MEM32 = base_mem /\\ s R_A0 = arr /\\ s R_A1 = key /\\ s R_A2 = len /\\ "
                 "(4 * len < 2^32) /\\ (exists k', arr = 4 * k') /\\ cycle_count_of_trace t' = 0)")
    inv = (
        "Definition se_find_ge_timing_invs (arr key len : N) (base_mem : memory) (t:trace) "
        ": option Prop :=\n"
        "match t with (Addr a, s) :: t' => match a with\n"
        f"| 0x0 => Some {entry_inv}\n| 0x1c => Some {body_inv}\n"
        f"| 0x2c => Some ({P}timing_postcondition base_mem arr key len t)\n"
        f"| 0x30 => Some ({P}timing_postcondition base_mem arr key len t)\n"
        "| _ => None end | _ => None end.")

    (ws / "targets" / "Se_find_ge_gen.v").write_text(
        render(spec, inv, "se_find_ge_timing_invs", proof_body=None))
    print("wrote proofs/targets/{se_find_ge_lifted,Se_find_ge_gen}.v  "
          "-> now `docker restart docker-rocq-1`, then drive the closer.")


if __name__ == "__main__":
    main()
