(* FROZEN held-out fixture — the Opus-4.8-synthesized loop invariant for ap_sum_u32
   (sum of a uint32 array, -O2, RISC-V / NEORV32). Captured verbatim from the held-out
   synthesis experiment (see docs/RESULTS.md); do NOT regenerate via the LLM. Only the
   timing-invariant block below is consumed (eval.targets._extract_invariant); this file
   is a fixture, not a compilation unit.

   Shape notes that stress the generic discharge closer:
     * -O2 emitted `beq a1,zero` -> TWO exits with different exact costs
       (0x24 normal fall-through, 0x2c zero-trip guard) -> per-exit `=` posts;
     * the induction register is R_A5 (from `addi a5,a0,0`), not ct_swap's R_A2/R_A0;
     * the body arm is 1-indexed / strict (`i < len`), witness `1 + i` at the step and
       `0` at the base;
     * the loop guard is a running-pointer test `bne a5,a3` with
       a5 = arr (+) 4*i, a3 = arr (+) 4*len (pointer-equality -> index-equality). *)
Definition ap_sum_u32_timing_invs (arr len : N) (t:trace) : option Prop :=
match t with (Addr a, s) :: t' => match a with
| 0x0 => Some (s R_A0 = arr /\ s R_A1 = len /\ 4 * len < 2^32 /\ (exists k, arr = 4 * k) /\ cycle_count_of_trace t' = 0)
| 0x14 => Some (exists i, i < len /\ 4 * len < 2^32 /\ s R_A5 = arr ⊕ (4 * i) /\ s R_A3 = arr ⊕ (4 * len) /\
        cycle_count_of_trace t' = tfbeq + tslli 2 + taddi + tadd + taddi + i * (tlw + taddi + tadd + ttbne))
| 0x24 => Some (cycle_count_of_trace t' = tfbeq + tslli 2 + taddi + tadd + taddi + (len - 1) * (tlw + taddi + tadd + ttbne) + (tlw + taddi + tadd + tfbne))
| 0x2c => Some (cycle_count_of_trace t' = taddi + ttbeq)
| _ => None end | _ => None end.
