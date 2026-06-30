# cloq-agent held-out generalization test set

A stratified set of **novel** C functions for exercising the new discharge
machinery (Phase 1 array/pointer, Phase 2 search decidability, Phase 3 aliasing)
and measuring whether it **generalizes** rather than recalls. None of these is a
corpus target, so a pass is generalization, not memorization.

## Why these, and why held-out

`docs/RESULTS.md` is explicit that a number only means something when the gold is
withheld from the proof library and the few-shot. So every function here is
new, but each sits in a **known structural class** the corpus already has a gold
proof for. The set is stratified so you can read a per-class pass rate, not one
blended number:

| tier | class | unblocked by | expect |
|------|-------|--------------|--------|
| A | straight-line / counter loop | already works | prove (regression guard) |
| B | array/pointer loop, no early exit | Phase 1 | prove |
| C | search, data-dependent early exit | Phase 2 | prove |
| D | memory-aliasing branch | Phase 3 | prove |
| E | nested loop / cyclic list | n/a | **stay ceiling** |

Tier E is not filler: if anything in E flips to PROVED, that is a soundness
alarm (a vacuous premise, or a template firing where it must not), not progress.

## Compile / lift notes (pinned toolchain)

```
riscv64-unknown-elf-gcc 14.2.0 \
  -march=rv32im_zicsr_zicntr -mabi=ilp32 -O2 -ffreestanding -nostdlib
```

- `-O2` on `rv32im` does **not** auto-vectorize (no V extension) and does not
  aggressively unroll, so the loops survive as loops. If a build does unroll,
  add `-fno-unroll-loops`.
- Loop-idiom recognition can turn a copy/scan loop into a `memcpy`/`memset`/
  `memchr` **libcall**, which the lifter can't see. The bodies here are written
  to avoid that (arithmetic in the body, not a bare copy). For
  `se_first_zero_u8` specifically, compile with
  `-fno-tree-loop-distribute-patterns` if your build emits a `memchr` call.
- `mul` is used by `ap_scale_inplace` / `ap_dot2` and is a real `rv32im`
  instruction — confirm the NEORV32 timing model carries `tmul`.

## Run protocol — the part that decides "does it generalize"

Run each target **twice** and compare. This is the whole point.

1. **RAG on, corpus intact** (LLM may retrieve the analogous gold):
   ```
   cloq-agent prove-c tierB_array_ptr.c --func ap_sum_u32
   ```
   A pass means: generalizes *given a retrieved analogue*.

2. **Nearest analogue ablated** (recall removed):
   ```
   cloq-agent prove-c tierB_array_ptr.c --func ap_sum_u32 \
       --ablate-gold-proof ct_swap
   cloq-agent prove-c tierC_search.c --func se_find_eq \
       --ablate-gold-proof find_in_array
   ```
   A pass means: generalizes *without* the near neighbour — the strong signal.

If a target passes (1) but fails (2), you have recall, not synthesis. Track the
gap per tier; that gap is the headline metric. `se_find_eq` and `ap_sum_u32`
exist specifically as the recall-vs-synthesis controls (they sit closest to a
corpus function).

## Validation without an FPGA

There is no hardware oracle in this configuration, so two gates carry the
soundness weight:

- **Mutation** (kept): inject a data-dependent term into the loop body / claim;
  every `expected: prove` target's proof MUST then fail. A target whose mutated
  proof still closes is vacuous — flag it.
- **Premise satisfiability** (new, replaces the FPGA's "is this real" role): for
  every assumed premise in `manifest.yaml` (`PTR_ALIGN`, `LEN_VALID`, `NVL`,
  ...), emit and discharge `exists state/memory, <premise>`, or instantiate at a
  concrete model. Mutation does not catch a vacuously-true theorem caused by a
  false *premise*; this gate does. This matters most in Tier D, where `noverlaps`
  is assumed and a wrong synthesized region model is the failure mode.

Constant-time (`ct_cond_not`, Tier B) is verified purely formally: the secret
(`mask`) must be absent from the cycle-count closed form, which `spec_lint`
already enforces. Dropping the FPGA loses only the empirical cross-check, not the
formal guarantee — but say so in the report.

## What a healthy result looks like

- Tier A: 2/2 (else a regression — stop and fix the discharge change).
- Tier B: climbs to 6/6 as Phase 1 lands; the ablated run lags the intact run at
  first, then closes the gap.
- Tier C: climbs to 3/3 as the dec-template lands; `se_find_ge` (different
  predicate) passing ablated is the real proof the template generalized.
- Tier D: the hardest; treat any ablated pass here as a milestone, and re-check
  the premise-satisfiability gate fired.
- Tier E: 0/2 PROVED, both reported as fast ceiling diagnostics. Stays 0/2.

## Files

```
tierA_controls.c     sl_sum3, cl_countdown
tierB_array_ptr.c    ap_sum_u32, ap_sum_u8, ap_scale_inplace, ap_dot2,
                     ap_ptr_walk, ct_cond_not
tierC_search.c       se_find_ge, se_first_zero_u8, se_find_eq
tierD_aliasing.c     al_swap_a, al_unlink
tierE_negative.c     neg_matmul_trace, neg_cyclic_find
manifest.yaml        per-func class / premises / expected outcome / fix exercised
```
