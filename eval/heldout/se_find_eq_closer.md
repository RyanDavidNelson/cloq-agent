# se_find_eq bottom-test closer — COMPLETE (standalone coqc-Qed `.vo`)

Status: **the held-out se_find_eq search proof is a kernel-checked `.vo`.** A clean `coqc` of
`proofs/targets/Se_find_eq_gen.v` emits `Se_find_eq_gen.vo` with a real `Qed` (no "Open proofs
remain"). `Print Assumptions` rests only on the documented trust basis — `functional_extensionality`
(Picinae's standard axiom) and the NEORV32 latency constants (`T_inst_latency` / `T_data_latency`) —
no `admit`, no proof-specific axiom. This is the first held-out search function the engine closes
that it has never seen: generated program, emitted (`cloq_`-namespaced) decidability, emitted
bottom-test `time_of`, body invariant, `destruct len` not-found-exit closer. Pinned by
`tests/test_se_find_eq_closes.py`.

What turned "closes in petanque" into a standalone `.vo` (the former CAVEAT, now resolved): the
interactive flow used NUMBERED focus brackets (`3:{ }` / `1:{ }`) that select goals by position, and
those do not linearise in a flat `Proof. ... Qed.` Two facts the numbered flow had papered over:

* each `repeat (tstep r5_step)` on the `0x1c` body fans into THREE leaves — found-here / bottom-test
  exit / i+1 continuation — and the `cloq_key_in_array_dec` case-split doubles that into IN and
  NOT_IN. The fix is one **order-independent** `all: solve [ closer1 | closer2 | closer3 ]` per
  branch (each closer only fully succeeds on its own leaf, so leaf order is irrelevant), under a
  curly-brace `{ ... }` focus that isolates each `apply prove_invs` / `destruct_inv` arm. No numbered
  positional focus survives.
* the `len = 0` guard-taken path parks two logically-unconstrained `N` metavariables on the shelf
  (`tstep` introduces them; the immediate-exit path never pins them via an `exists` witness, the way
  the len<>0 search arm does). They live only in the proof term, never the statement, so a closing
  `Unshelve. all: exact 0.` discharges them soundly.

Reproduce: `python eval/heldout/build_se_find_eq.py` regenerates the OPEN scaffold (+ compiles the
program functor); the committed `Se_find_eq_gen.v` carries the closed `Proof. ... Qed.` Compile it
with `coqc` under the project `_CoqProject` load path (e.g. `make all` in `proofs/`, or the steps in
`tests/test_se_find_eq_closes.py`). The historical petanque tactic script (NUMBERED focus, kept for
reference) follows; the live, linearised proof is the `.v` itself.

Key lessons baked in: invariant carries `4*len < 2^32` (no-wrap reductions need it in every arm);
memory clauses use `arr + 4*j` (plain, matches the emitted template) while the moving pointer keeps
`arr (+) 4*i` (the real modular machine value); the modular bridge is `rewrite (N.mod_small (1 + i))
by lia` (explicit arg, as in find_in_array); the bottom-test latch `if len =? 1+i then ttbeq else
tfbeq` is reduced with `rewrite ?Hf` / `rewrite ?Ht` before `hammer`; `key_in_array_dec` is over
`s' V_MEM32` so `rewrite Memeq` aligns it with the `base_mem` clauses.

```coq
intros.
destruct (len =? 0) eqn:LEN0.
2:{ apply N.eqb_neq in LEN0. apply prove_invs.
    (* BASE 0x0 *)
    simpl. rewrite ENTRY. unfold entry_addr. tstep r5_step.
      now (repeat split; (try assumption); (try reflexivity)).
    (* inductive setup *)
    intros. eapply startof_prefix in ENTRY; try eassumption.
    eapply preservation_exec_prog in MDL; try eassumption; [idtac|apply lift_riscv_welltyped].
    clear - ENTRY PRE MDL LEN0. rename t1 into t. rename s1 into s'.
    destruct_inv 32 PRE.
    (* ARM 0 : 0x0 -> body. guard-taken branch is contradictory (len<>0); body inv at i=0 *)
    destruct PRE as (Mem & A0 & A1 & A2 & LV & PA & Cyc). repeat (tstep r5_step).
    apply N.eqb_eq in BC. contradiction.
    exists 0. apply N.eqb_neq in BC.
      repeat split; (try assumption); (try reflexivity); (try lia); (try (intros; lia));
      (try (psimpl; lia)); (try hammer).
    (* ARM 1 : 0x1c body step *)
    destruct PRE as (i & Hlt & LV2 & A4 & A5 & A1eq & A0eq & A2eq & Memeq & NotFound & Cyc).
    destruct (cloq_key_in_array_dec (s' V_MEM32) arr key len) as [IN | NOT_IN].
    - (* IN *) repeat (tstep r5_step).
      (* found-here (goal 3): *)
      3:{ left. exists i. split. assumption.
          split. apply negb_false_iff in BC. now apply N.eqb_eq in BC.
          split. exact NotFound. unfold cloq_time_of_se_find_eq. hammer. }
      (* not-found/bound-exit (contradictory on IN): *)
      1:{ exfalso. destruct IN as (idx & IL & IE). rewrite Memeq in IE.
          apply N.eqb_eq in BC0. rewrite N.mod_small in BC0 by lia.
          destruct (N.lt_ge_cases idx i) as [L|G]. exact (NotFound idx L IE).
          assert (idx = i) by lia. subst idx.
          apply negb_true_iff in BC. apply N.eqb_neq in BC. exact (BC IE). }
      (* i+1 continuation: *)
      apply negb_true_iff in BC. apply N.eqb_neq in BC. exists (1 (+) i).
      repeat split; (try reflexivity); (try assumption);
        (try (apply N.eqb_neq in BC0; psimpl; lia));
        (try (intros j Hj; destruct (N.lt_ge_cases j i) as [Lj|Gj];
              [ now apply (NotFound j Lj)
              | assert (j = i) as Ej by (psimpl in Hj; lia); subst j; exact BC ])).
      apply N.eqb_neq in BC0. rewrite (N.mod_small (1 + i)) in BC0 by lia.
      rewrite (N.mod_small (1 + i)) by lia.
      assert (len =? 1 + i = false) as Hf by (now apply N.eqb_neq). rewrite ?Hf. hammer.
    - (* NOT_IN *) repeat (tstep r5_step).
      (* found-here on NOT_IN -> contradiction (goal 3): *)
      3:{ exfalso. apply NOT_IN. exists i. split. assumption.
          rewrite Memeq. apply negb_false_iff in BC. now apply N.eqb_eq in BC. }
      (* i+1 continuation (goal 2): same closer as the IN continuation *)
      2:{ apply negb_true_iff in BC. apply N.eqb_neq in BC. exists (1 (+) i).
          repeat split; (try reflexivity); (try assumption);
            (try (apply N.eqb_neq in BC0; psimpl; lia));
            (try (intros j Hj; destruct (N.lt_ge_cases j i) as [Lj|Gj];
                  [ now apply (NotFound j Lj)
                  | assert (j = i) as Ej by (psimpl in Hj; lia); subst j; exact BC ])).
          apply N.eqb_neq in BC0. rewrite (N.mod_small (1 + i)) in BC0 by lia.
          rewrite (N.mod_small (1 + i)) by lia.
          assert (len =? 1 + i = false) as Hf by (now apply N.eqb_neq). rewrite ?Hf. hammer. }
      (* not-found EXIT (the i = len-1 dual): *)
      right. split. intro H. apply NOT_IN. rewrite Memeq. exact H.
      apply N.eqb_eq in BC0. rewrite (N.mod_small (1 + i)) in BC0 by lia. rewrite BC0.
      unfold cloq_time_of_se_find_eq. hammer.
      assert (len =? 1 (+) i = true) as Ht by
        (apply N.eqb_eq; rewrite (N.mod_small (1 + i)) by lia; exact BC0). rewrite ?Ht. hammer.
}
(* len = 0 GUARD branch: *)
apply N.eqb_eq in LEN0. subst len. apply prove_invs.
  simpl. rewrite ENTRY. unfold entry_addr. tstep r5_step.
    now (repeat split; (try assumption); (try reflexivity)).
  intros. eapply startof_prefix in ENTRY; try eassumption.
  eapply preservation_exec_prog in MDL; try eassumption; [idtac|apply lift_riscv_welltyped].
  clear - ENTRY PRE MDL LEN0. rename t1 into t. rename s1 into s'.
  destruct_inv 32 PRE.
  2:{ destruct PRE as (i & Hlt & rest). exfalso. lia. }   (* 0x1c vacuous: i < s R_A2 = 0 *)
  (* len=0 arm 0: guard split. *)
  destruct PRE as (Mem & A0 & A1 & A2 & LVx & PA & Cyc). repeat (tstep r5_step).
  2:{ exfalso. apply N.eqb_neq in BC. exact (BC LEN0). }   (* fallthrough: s R_A2 <> 0 vs LEN0 *)
  right. split.
  intro H. destruct H as (k & Hc & _). rewrite LEN0 in Hc. lia.   (* ~exists (len=0) *)
  unfold cloq_time_of_se_find_eq. rewrite LEN0. hammer.           (* time_of None at len=0 = guard *)
```

After the final `hammer`, petanque reports `goals == 0` — the proof is complete.

`(+)` above is the Picinae `⊕` (mod-2^32 add); kept ASCII here for the markdown.
