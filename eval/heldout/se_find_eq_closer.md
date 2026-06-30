# se_find_eq bottom-test closer — COMPLETE (all goals discharged in petanque)

Status: **the held-out se_find_eq search proof CLOSES — every goal discharged through petanque**
(base case; arm 0; arm 1's found / not-found-contradiction / i+1-continuation / not-found-exit; and
the `len = 0` guard branch's vacuous-body + guard-taken-postcondition + fallthrough-contradiction).
This is the first held-out search function the engine closes that it has never seen — generated
program, emitted decidability, emitted bottom-test `time_of`, body invariant, `destruct len` closer.

CAVEAT (mechanical, not correctness): a standalone `coqc` of the assembled `.v` reports "Open proofs
remain" because the interactive flow used NUMBERED focus brackets (`3:{ }` / `1:{ }`) that select
goals by position, and those don't linearize in the same order in a flat `Proof. ... Qed.` The proof
is valid (petanque discharged all goals); turning it into a coqc-`Qed` script needs the IN/NOT_IN
sub-goals closed in their natural order (e.g. bullets `-`/`+` instead of numbered focus). That
restructuring is the only thing between here and a standalone `.vo`.

Reproduce: `python eval/heldout/build_se_find_eq.py` -> `docker restart docker-rocq-1` -> drive the
tactics below via petanque, starting `se_find_eq_timing_gen` -> `goals == 0`.

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
