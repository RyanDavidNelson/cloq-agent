(* Addloop.v — pipeline smoke target.

   Faithful transcription of the Peano-addition timing proof from
   Averill, "Formally-Verified, Tight Timing Constraints for Machine Code" (PLDI SRC '25),
   Figures 2-4. This file serves two purposes:
     1. an end-to-end check that lift -> invariant -> whammer -> Qed works against the
        vendored Picinae/Cloq, with NO LLM in the loop;
     2. the `gold_invariant` source the eval harness extracts for the addloop target.

   PORTING NOTE: the identifiers below (Picinae_riscv, the lifted `addloop` program map,
   ML, cycle_count, satisfies_all, addloop_exit, R_T0/R_T1, step/psimpl/whammer) must match the
   exact names exported by your `vendor/picinae` checkout + Cloq timing layer. Generate the
   lifted `Definition addloop : program := ...` with the Picinae lifter from the assembled
   binary (see eval/targets/addloop.objdump for the source assembly). *)

Require Import Picinae_riscv.
Require Import TimingAutomation.

(* The lifted program. Produced by the Picinae lifter; shown here as the source assembly:

     add:
       beqz t0, end     ; 0   - goto end if t0 == 0
       addi t1, t1, 1   ; 4   - increment t1
       addi t0, t0, -1  ; 8   - decrement t0
       j    add         ; 12  - goto add
     end:                ; 16

   Replace the Admitted stub with the lifter's output: *)
Parameter addloop : program.
Parameter addloop_exit : addr -> Prop.

(* Invariant set (Figure 3). One precondition arm (addr 0), one postcondition arm (addr 16).
   tb = time of a taken branch; ft = time of a fall-through; the loop body costs
   (ft + 2 + 2 + tb) cycles per iteration on this timing model. *)
Definition timing_invs (p:addr) (x y:N) (t:trace) :=
  let tb := 5 + (ML - 1) in   (* time of a taken branch *)
  let ft := 3 in              (* time of a fallen-through branch *)
  match t with
  | (Addr a, s) :: t' =>
      match a with
      | 0  => Some (s R_T0 <= x /\
                    cycle_count t' = (x - s R_T0) * (ft + 2 + 2 + tb))
      | 16 => Some (cycle_count t' = tb + x * (ft + 2 + 2 + tb))
      | _  => None
      end
  | _ => None
  end.

(* Timing theorem (Figure 4). The proof is almost entirely Cloq automation. *)
Theorem addloop_timing :
  forall s p t,
  satisfies_all addloop (timing_invs p (s R_T0) (s R_T1)) addloop_exit t.
Proof.
  (* Address 4 *) repeat step; psimpl; subst; lia.
  (* Address 8 (break / loop cases) *) whammer.
  (* Postcondition *) whammer.
Qed.
