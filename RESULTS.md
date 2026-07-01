# Manifest run results — `MANIFEST.md` / `manifest.yaml` held-out set

Each function in the manifest was put through the real `prove-c` pipeline
(**compile → lift → classify → prove**) on the pinned NEORV32 toolchain. For every
function below: the source it was compiled from, whether Rocq **closed the timing
theorem**, the proven closed-form cycle count (when proved), and — when not — a brief
diagnostic naming the ceiling class it hit.

This is the per-target run log for the manifest test set; the project-level capability
write-up is `docs/RESULTS.md`.

> **UPDATE (2026-06-30) — the loop class now closes end-to-end.** After the discharge
> robustness work (`solve_timing_loop`, Phase 1/1b) AND a synthesis-scaffold correctness fix
> (the invariant match bound `a` shadowed a *parameter* named `a`, silently corrupting every
> `ap_*` invariant — now a non-colliding `pc`), the array/pointer class was re-run through the
> skeleton-synthesis path with Claude Opus 4.8. **5 of 6 Tier-B array/pointer functions now
> reach Qed end-to-end** (LLM writes the invariant, Rocq checks it) — including the
> constant-time `ct_cond_not`. See the [end-to-end run](#frontier-model--skeleton-synthesis-experiment)
> at the bottom. Search loops (Tier C) still need the Phase-2 decidability case-split.

## How these were produced

- **Toolchain (pinned):** `riscv64-unknown-elf-gcc 13.2.0`,
  `-march=rv32im_zicsr_zicntr -mabi=ilp32 -O2 -ffreestanding -nostdlib`.
- **Prover:** Rocq/Coq 8.20.1 + the prebuilt vendored Picinæ/Cloq, via the petanque
  server. Invariant synthesis (for the loop classes) used the local `qwen3-coder:30b`
  model at **full budget** — 12 invariant proposals, 600-step backtracking proof
  search, verifier-guided refinement — against the **real 3604-record RAG corpus**.
  (No frontier-model escalation: no `CLOQ_API_KEY` is configured.)
- **Two passes:** Tier A/D ran in the default mode (deterministic CFG-derived
  invariant, no LLM). Tier B/C were re-run with `--force-synthesis` to actually
  *attempt* the held-out synthesis; the result of that full-budget attempt is reported.
- **Trust basis (unchanged):** proofs are sound *relative to* the NEORV32 timing model
  (a trusted, hardware-unvalidated input — FPGA parked) and the pinned flags. The model
  only proposes; Rocq checks.

## Summary

| # | function | tier | class (lifter) | property | manifest expects | **result** | proven closed form |
|---|----------|------|----------------|----------|------------------|------------|--------------------|
| 1 | `sl_sum3` | A | straight-line | wcet | prove | ✅ **PROVED** | `cycle ≤ tadd + tadd` (= 4) |
| 2 | `cl_countdown` | A | straight-line † | wcet | prove | ✅ **PROVED** † | `cycle ≤ 0` (loop optimized away) |
| 3 | `ap_sum_u32` | B | array/pointer loop | wcet | prove | ✅ **PROVED** (LLM) | `pre + (len-1)·body + tail`, `body = tlw+taddi+tadd+ttbne` |
| 4 | `ap_sum_u8` | B | array/pointer loop | wcet | prove | ❌ synthesis | discharge OK; model adds a spurious `PTR_ALIGN`/extra arm (byte stride has no alignment) |
| 5 | `ap_scale_inplace` | B | array/pointer loop | wcet | prove | ✅ **PROVED** (LLM) | store-in-loop closed form in `len` (`tmul` per iter) |
| 6 | `ap_dot2` | B | array/pointer loop | wcet | prove | ✅ **PROVED** (LLM) | two-pointer closed form in `len` (`tlw+tlw+tmul` per iter) |
| 7 | `ap_ptr_walk` | B | array/pointer loop | wcet | prove | ✅ **PROVED** (LLM) | pointer-increment closed form in `count` |
| 8 | `ct_cond_not` | B | array/pointer loop | **ct** | prove | ✅ **PROVED** (LLM) | closed form in `len`, **secret `mask`-independent** (spec_lint-enforced) |
| 9 | `se_find_ge` | C | search early-exit | wcet | prove | ❌ ceiling (Phase 2) | needs the emitted decidability case-split (generic loop closer has no `destruct … _dec`) |
| 10 | `se_first_zero_u8` | C | search early-exit | wcet | prove | ❌ ceiling (Phase 2) | search decidability case-split (byte-width predicate) |
| 11 | `se_find_eq` | C | search early-exit | wcet | prove | ❌ ceiling (Phase 2) | search decidability case-split |
| 12 | `al_swap_a` | D | straight-line ‡ | wcet | prove | ✅ **PROVED** ‡ | `cycle ≤ 2·tlw + 2·tsw` |
| 13 | `al_unlink` | D | straight-line ‡ | wcet | prove | ✅ **PROVED** ‡ | `cycle ≤ 2·tlw + 2·tsw` |
| 14 | `neg_matmul_trace` | E | unsupported control flow | wcet | **ceiling** | ✅ ceiling (correct) | — |
| 15 | `neg_cyclic_find` | E | search early-exit | wcet | **ceiling** | ✅ ceiling (correct) | — |

**Score:** **9/13** of the `expected: prove` targets close (was 4/13); both `expected: ceiling`
targets correctly stay at the ceiling. The four straight-line/Tier-D targets close
**deterministically (no LLM)**; the **five array/pointer loops** (`ap_sum_u32`,
`ap_scale_inplace`, `ap_dot2`, `ap_ptr_walk`, `ct_cond_not`) now close **end-to-end with the
LLM** — Claude Opus 4.8 writes the `exists`-index invariant under the CFG skeleton, and the
generic `solve_timing_loop` discharges it to Qed. The remaining gaps are honest and specific:
`ap_sum_u8` fails on *synthesis compliance* (the model over-copies a `PTR_ALIGN` premise a
byte-stride loop doesn't have, and annotates a spurious pass-through arm — discharge itself is
fine), and the three Tier-C search loops need the Phase-2 decidability case-split, which the
generic loop closer does not emit.

Two honest caveats are flagged in the table and detailed below:
- **†** `cl_countdown` proved, but `-O2` collapsed the counter loop to a single `ret`, so
  it did **not** exercise counter-loop discharge (it degenerated to straight-line).
- **‡** `al_swap_a` / `al_unlink` proved as **straight-line** because the *timing* of a
  branchless swap is a fixed instruction sequence; memory aliasing affects value
  correctness, not the cycle count, so the Tier-D `noverlaps` machinery was never needed
  for the WCET claim.

---

## Tier A — controls (`tierA_controls.c`)

### 1. `sl_sum3` — ✅ PROVED
```c
unsigned int sl_sum3(unsigned int x, unsigned int y, unsigned int z) {
    unsigned int s = x + y;
    s = s + z;
    return s;
}
```
Straight-line; compiles to two `add`s + `ret`. **Closed form `cycle ≤ tadd + tadd`
= 4 cycles (exact for `NEORV32BaseConfig`).** Closed by the deterministic
`repeat (tstep r5_step) …` driver, no LLM. Premises jointly satisfiable;
postcondition non-vacuous (mutation gate).

### 2. `cl_countdown` — ✅ PROVED (degenerate; see caveat †)
```c
unsigned int cl_countdown(unsigned int n) {
    unsigned int c = 0;
    while (n > 0) { c = c + 1; n = n - 1; }
    return c;
}
```
The loop computes `c == n`, and `n` is already in `a0`, so `-O2` eliminated the loop
entirely — the function compiles to a single `jalr zero,0(ra)` (`ret`). It therefore
**lifts as straight-line, not a counter loop**, and proves trivially with `cycle ≤ 0`.
**Diagnostic:** PROVED, but this did *not* test counter-loop discharge as the manifest
intended (`counter-loop-generalizes-off-addloop`). To exercise that, the loop body must
have an effect `-O2` cannot fold away (e.g. a side effect or an opaque accumulator). The
genuine counter-loop case remains `addloop` in the corpus.

---

## Tier B — array/pointer loops (`tierB_array_ptr.c`) — all ❌ ceiling

All six lift correctly as **array/pointer loop** and the CFG-derived per-iteration body
is recovered (shown per function). The full-budget forced run made **12 LLM invariant
proposals** each against the real corpus; **all stalled with `iters=0`** — i.e. proof
search never even started, because every proposed invariant **failed to typecheck** at
`driver.start`. Representative proposal (attempt 1):

```coq
| 0x14 => Some (cycle_count_of_trace t' <= (c0 - c) * t_body)   (* c0, c, t_body unbound *)
```

**Root cause (an integration gap, not a hard ceiling).** The shape is right, but the model
is running in **freeform mode** — inventing the whole `Definition` including registers and
timing constants — so it emits free variables (`c0`, `t_body`, and even invalid register
names like `R_R0`) that do not typecheck. The repo *has* a much stronger **skeleton
synthesis** path (`lift/cfg.py:skeleton_plan`, `agent/invariant_synth.py`) where the CFG
pins the match structure, addresses, and the **computed** per-iteration body time, and the
model fills only the loop-invariant hole — but the `prove-c` **pipeline never builds or
passes that skeleton** (`pipeline.py` calls `orch.prove(...)` with no `invariant_skeleton`,
so `orchestrator.py:152` falls back to `mode="freeform"`). And skeleton mode can't even be
*built* for these classes yet, because `intake.lift` derives a **pinned exit postcondition**
only for the straight-line/counter-loop classes, and the array/pointer theorem additionally
needs entry hypotheses (`PTR_ALIGN`, `LEN_VALID`, and the base/bound register ties — cf. the
gold `ct_swap` theorem's `ALIGNED/LEN/A2/A3`) that the generic C intake does not emit.
Per `docs/RESULTS.md` the *discharge* layer for this class is solved (Phase 1, ct_swap), so
what remains is wiring **skeleton synthesis + a derived loop postcondition + entry-hypothesis
emission** into `prove-c` — the project's stated open task — or escalating invariant
synthesis to a frontier model. The local 30B model freeforming alone does not get there.

### 3. `ap_sum_u32`
```c
unsigned int ap_sum_u32(unsigned int *a, unsigned int len) {
    unsigned int s = 0, i;
    for (i = 0; i < len; i++) s = s + a[i];
    return s;
}
```
Recovered loop body @0x14: `lw a4,0(a5); addi a5,a5,4; add a0,a0,a4; bne a5,a3,…`
(word-stride accumulate). Premises assumed: `LEN_VALID (4·len < 2³²)`, `PTR_ALIGN`.

### 4. `ap_sum_u8`
```c
unsigned int ap_sum_u8(unsigned char *a, unsigned int len) {
    unsigned int s = 0, i;
    for (i = 0; i < len; i++) s = s + a[i];
    return s;
}
```
Recovered loop body @0x40: `lbu a4,0(a5); addi a5,a5,1; add a0,a0,a4; bne a5,a1,…`
(byte stride — `lbu`, `+1`). Same class as `ap_sum_u32`, stride generalized.

### 5. `ap_scale_inplace`
```c
void ap_scale_inplace(unsigned int *a, unsigned int len, unsigned int k) {
    unsigned int i;
    for (i = 0; i < len; i++) a[i] = a[i] * k;
}
```
Recovered loop body @0x68: `lw a5,0(a0); addi a0,a0,4; mul a5,a5,a2; sw a5,-4(a0); bne …`.
Confirms a single-region **store in the loop** (`sw`) and a real `mul` (`rv32im`, exercises
`tmul`) — a store, not aliasing.

### 6. `ap_dot2`
```c
unsigned int ap_dot2(unsigned int *a, unsigned int *b, unsigned int len) {
    unsigned int s = 0, i;
    for (i = 0; i < len; i++) s = s + a[i] * b[i];
    return s;
}
```
Recovered loop body @0x94: two loads (`lw … a5`, `lw … a1`), two pointer bumps, `mul`,
`add` — two read-only base pointers, no aliasing concern.

### 7. `ap_ptr_walk`
```c
unsigned int ap_ptr_walk(unsigned int *p, unsigned int count) {
    unsigned int s = 0, i;
    for (i = 0; i < count; i++) { s = s + *p; p = p + 1; }
    return s;
}
```
Recovered loop body @0xcc: `lw a3,0(a5); addi a4,a4,1; addi a5,a5,4; add a0,a0,a3; bne …`
— pointer-increment induction with a separate counter bound.

### 8. `ct_cond_not` — constant-time (property `ct`, secret `mask`)
```c
void ct_cond_not(unsigned int *a, unsigned int len, unsigned int mask) {
    unsigned int i;
    for (i = 0; i < len; i++) a[i] = a[i] ^ mask;
}
```
Recovered loop body @0xf8: `lw a5,0(a0); addi a0,a0,4; xor a5,a5,a2; sw a5,-4(a0); bne …`.
The per-element work is branchless and identical on every path, so the cycle count is
structurally independent of `mask`. **Diagnostic:** same array/pointer ceiling — the CT
*obligation* (mask absent from the closed form, `spec_lint`-enforced) is well-posed, but
the underlying `cycle =` loop theorem needs the exists-index invariant first, which the
clamped synthesis did not emit.

---

## Tier C — search loops, data-dependent early exit (`tierC_search.c`) — all ❌ ceiling

All three lift as **search early-exit** (the CFG shows the bound increment in the loop
header and the data-dependent compare/branch as a second exit edge). The full-budget forced
run made **12 invariant proposals** each; **all stalled with `iters=0`** — same freeform
typecheck failure as Tier B (see root cause above), compounded by the harder claim. A WCET
over an early-exit search proves a *found / not-found disjunction*, forcing a
program-specific decidability case-split (`key_in_array_dec`-style). Phase 2 made that
case-split a *template* and drove a twin to Qed, and the held-out `se_find_eq`/`se_find_ge`
closers exist as standalone `.vo` in the corpus — but `prove-c` still doesn't wire the
skeleton + the disjunctive postcondition + the branch closer into the C-intake path, so the
freeform local model cannot assemble it. This is the hardest of the loop classes and the one
furthest from a wired end-to-end pass.

### 9. `se_find_ge`
```c
unsigned int se_find_ge(unsigned int *a, unsigned int len, unsigned int threshold) {
    unsigned int i;
    for (i = 0; i < len; i++) { if (a[i] >= threshold) return i; }
    return len;
}
```
`>=` predicate (the dec-template generalization case vs. `find_in_array`'s `==`). Two exit
edges lifted (`['0x2c', '0x30']`).

### 10. `se_first_zero_u8`
```c
unsigned int se_first_zero_u8(unsigned char *p, unsigned int len) {
    unsigned int i;
    for (i = 0; i < len; i++) { if (p[i] == 0) return i; }
    return len;
}
```
Byte width + `== 0` predicate. Lifted as search early-exit (no `memchr` libcall under the
pinned flags — the byte compare survived).

### 11. `se_find_eq`
```c
unsigned int se_find_eq(unsigned int *a, unsigned int len, unsigned int key) {
    unsigned int i;
    for (i = 0; i < len; i++) { if (a[i] == key) return i; }
    return len;
}
```
The recall-vs-synthesis control (closest to `find_in_array`). Lifted with two exits
(`['0x8c', '0x90']`); ceiling under the clamped budget.

---

## Tier D — "aliasing" branches (`tierD_aliasing.c`) — both ✅ PROVED (as straight-line)

### 12. `al_swap_a` — ✅ PROVED
```c
void al_swap_a(unsigned int *p, unsigned int *q) {
    unsigned int t = *p;
    *p = *q;
    *q = t;
}
```
Compiles to `lw; lw; sw; sw; ret` — branchless and loop-free. **Closed form
`cycle ≤ tlw + tlw + tsw + tsw` → 16 + 4·T_data_latency cycles (≥ 16).** Closed
deterministically, no LLM.

### 13. `al_unlink` — ✅ PROVED
```c
struct node { struct node *next; struct node *prev; unsigned int val; };
void al_unlink(struct node *n) {
    struct node *p = n->prev;
    struct node *q = n->next;
    p->next = q;
    q->prev = p;
}
```
Also lowers to a fixed `lw; lw; sw; sw; ret` (the field loads + the two link stores), same
**`cycle ≤ 2·tlw + 2·tsw`**.

**Diagnostic (caveat ‡):** both proved, but as the *straight-line* class, **not** the
Tier-D aliasing class the manifest anticipated (Phase 3 `noverlaps`). The reason is sound:
the **WCET is a fixed instruction sequence** regardless of whether the pointers alias —
aliasing changes the *values* stored, not the *cycle count*. So the `noverlaps` premise and
the `getmem_noverlap` lemma are not load-bearing for a timing-only theorem here; they would
be required for a functional-correctness postcondition, which is out of scope for WCET.
This is an honest pass, not a Phase-3 milestone, and the premise-satisfiability gate had
nothing aliasing-specific to discharge.

---

## Tier E — negatives (`tierE_negative.c`) — both correctly ❌ ceiling (as required)

The manifest requires these to **stay** at the ceiling; a PROVED here would be a soundness
alarm. Both failed fast with the correct structured diagnostic and **no spin**.

### 14. `neg_matmul_trace` — ✅ ceiling (correct)
```c
unsigned int neg_matmul_trace(unsigned int m, unsigned int n) {
    unsigned int t = 0, i, j;
    for (i = 0; i < m; i++)
        for (j = 0; j < n; j++)
            t = t + (i * n + j);
    return t;
}
```
Lifted and classified **unsupported control flow** (nested loops). Failed fast:
"nested/irreducible control flow is out of scope." Correct wall — the classifier did not
mis-template the nested loop.

### 15. `neg_cyclic_find` — ✅ ceiling (correct)
```c
struct lnode { struct lnode *next; unsigned int key; };
unsigned int neg_cyclic_find(struct lnode *head, unsigned int key, unsigned int guard) {
    struct lnode *cur = head;
    unsigned int steps = 0;
    while (steps < guard) {
        if (cur->key == key) return steps;
        cur = cur->next; steps = steps + 1;
    }
    return guard;
}
```
Classified **search early-exit** (the guard-counter bound + data-dependent compare). Failed
fast with the search ceiling diagnostic. Critically, the **array-search template did *not*
fire** on this list traversal (the trip count is a guard, not a readable array length) — the
cyclic-uniqueness case stays an open problem, exactly as required.

---

## Reproduce

The manifest C files live at the repo root (`tierA_controls.c` … `tierE_negative.c`). With
the rocq container up (petanque on `:8765`) and a model server reachable:

```
cloq-agent prove-c tierA_controls.c --func sl_sum3                 # PROVED
cloq-agent prove-c tierD_aliasing.c --func al_swap_a               # PROVED
cloq-agent prove-c tierB_array_ptr.c --func ap_sum_u32             # ceiling diagnostic
cloq-agent prove-c tierB_array_ptr.c --func ap_sum_u32 --force-synthesis   # attempt synthesis
cloq-agent prove-c tierB_array_ptr.c --func ct_cond_not --property ct --secret mask
cloq-agent prove-c tierC_search.c   --func se_find_eq --force-synthesis
cloq-agent prove-c tierE_negative.c --func neg_matmul_trace        # must stay ceiling
```

---

## Frontier-model + skeleton-synthesis experiment

> **RESOLVED (2026-06-30): the loop class now reaches Qed end-to-end.** The two blockers this
> section originally identified — (1) discharge residuals on the synthesized invariant and (2)
> the not-yet-built witness/exit machinery — are fixed. Re-running the Tier-B array/pointer set
> through the skeleton path with Opus 4.8 as the primary model (`api` profile) now yields **5/6
> at Qed**: `ap_sum_u32`, `ap_scale_inplace`, `ap_dot2`, `ap_ptr_walk`, and the constant-time
> `ct_cond_not`. Most close on the **first invariant proposal with zero tactic-repair calls**
> (`llm_calls=1`) — Opus writes the invariant, `solve_timing_loop` closes it. What changed:
> - **Discharge (Phase 1/1b, `intake.solve_timing_loop`):** per-exit exact posts, `is_var`-guarded
>   dual-position index witness (strict `i<len` → exit at `len-1`), branch-cost `if` reduction
>   under a normalized modulus, a nested-`exists` (alignment) closer, and a `shiftl`/mul-order
>   normalizer. Closes ct_swap, addloop, AND the held-out synthesized invariants.
> - **Synthesis scaffold correctness (`cfg._render_definition`):** the match bound the trace
>   address as `a`, which **shadowed** a parameter named `a` (every `ap_*` target). `s R_A0 = a`
>   then meant "= the address", not the array base — a *silently type-checking but wrong*
>   invariant that no discharge could close. Fixed to a fresh non-colliding `pc`. **This one bug
>   was the difference between 0/6 and 5/6.**
> - **Synthesis robustness (`invariant_synth._splice_skeleton`):** accept a superset of the
>   required arms (drop a spurious model-added pass-through arm) instead of rejecting outright.
> - **Guidance:** the skeleton hole hints now tell the model to carry the entry hypotheses the
>   inductive step clears (register ties + no-wrap bound), use a strict bottom-test bound, and
>   exclude the ret's `tjalr` from the exit cost.
>
> Remaining: `ap_sum_u8` (byte stride) fails on synthesis *compliance* only (model over-copies a
> `PTR_ALIGN` premise it doesn't have); Tier-C search loops still need the Phase-2 decidability
> case-split. The accounting table at the very bottom is superseded by this note.

The initial loop-class failures above were partly a **mis-wiring**, not a hard limit. This
section re-runs the array/pointer class with the agent's good machinery actually engaged, and
escalates invariant synthesis to a frontier model. It answers the question the first run
couldn't: *can the AI agent + invariant synthesis do the job when wired correctly?*

### What was changed

1. **A real bug fix (committed):** `models.py` sent the *primary* model's `temperature` on
   the **escalation** call too, but `claude-opus-4-8` rejects `temperature` outright ("deprecated
   for this model") — so escalation 400'd every time and silently never happened. Fixed to omit
   `temperature` whenever escalating. (`src/cloq_agent/models.py`.)
2. **Skeleton synthesis engaged.** Instead of freeform (model invents the whole `Definition`),
   the run builds the CFG `skeleton_plan` — match scaffold + addresses + the **computed**
   per-iteration body timing pinned, model fills only the loop-invariant hole — and passes it as
   `invariant_skeleton`, plus the manifest's params + premises (the "properly configured target"
   data). Escalation to Opus 4.8 is enabled (attempts ≥ 2 use the frontier model with the prior
   attempt's Rocq error fed back).

### Result: invariant synthesis is now solved (the wall moved to discharge)

For `ap_sum_u32`, skeleton mode + Opus produced a **fully type-checking, correct-shaped**
invariant — the exact thing local-freeform never managed:

```coq
| 0x14 => Some (exists i, i <= len /\ s R_A5 = arr ⊕ i * 4 /\ s R_A3 = arr ⊕ len * 4 /\
        cycle_count_of_trace t' =
          tfbeq + tslli 2 + taddi + tadd + taddi + i * (tlw + taddi + tadd + ttbne))
```

It even reasoned out subtleties a naive CFG sum misses — e.g. a *freeform* Opus attempt wrote a
per-exit invariant that correctly attributes the **last loop iteration's branch as fall-through
(`tfbne`) not taken (`ttbne`)**, plus the final `tjalr` (ret):
`(len-1)*(tlw+taddi+tadd+ttbne) + tlw+taddi+tadd+tfbne+tjalr`. That is genuine timing reasoning,
not pattern-matching.

The generated theorem **compiles** and `driver.start` accepts it; proof search then runs (the
escalated tactic-repair makes 60–100+ Opus calls per target). So the bottleneck is no longer
*"the model can't write an invariant"* — it's now **proof discharge**.

### Where discharge stalls (the precise residuals)

Driving the documented `solve_timing_loop` closer on the typechecked invariant gets within a
couple of goals:

- **Zero-trip guard exit (`len==0`).** `-O2` emits a `beq len,0` guard, so the loop has *two*
  exits with different exact costs. A single shared `<=` worst-case bound is **not provable
  against the abstract NEORV32 timing constants** at the cheap exit. **Fix that works:** give
  each exit its **own exact `=` postcondition** (zero-trip arm = `taddi + ttbeq`); that arm then
  closes and drops out of the residuals. (The `0 < len` premise also fixes it logically, but the
  premise-satisfiability gate conservatively rejects non-zero-witness premises — see
  `premise_check.py`'s own note — so per-exit exact posts are the right lever.)
- **Loop witness not instantiated.** `solve_timing_loop`'s `handle_ex` does an `eexists`
  *before* the explicit `first [ exists (1+i) | exists 0 ]`, so on this program's goal shape the
  index is left as an evar (`?n2 <= len`, `s R_A0 = arr ⊕ ?n2 * 4`). Reordering (instantiate the
  witness first) advances it, but then the invariant's index convention and the closer must agree
  (a 1-indexed Opus invariant vs a 0-indexed `exists 0` produces `1 <= 0`).
- **Exit constant + register tracking.** Closing fully requires the invariant's exit constants
  (last-iteration `tfbne` + `tjalr`) and the moving-pointer register (`psimpl` rewrites
  `R_A5`↔`R_A0` through the `addi a5,a0,0` setup) to line up with what the closer proves.

These are exactly the project's stated open items in `docs/RESULTS.md` — the **"uniform generic
branch closer + generated (not reused) exit timing"** — now isolated to specific, named tactic
work rather than "the LLM can't do it."

### Frontier-model accounting (as requested)

| outcome | count | model |
|---|---|---|
| **Full Qed proofs** (this whole exercise) | **4** | **none** — all 4 (`sl_sum3`, `al_swap_a`, `al_unlink`, degenerate `cl_countdown`) close via the deterministic, no-LLM straight-line path |
| Loop-class targets reaching Qed | **0** | — (local *or* frontier) |
| Loop targets where the frontier model produced a **correct, type-checking invariant** (local produced **0**) | array/pointer class (verified on `ap_sum_u32`) | **claude-opus-4-8** |

**Bottom line:** **zero** proofs *required* the frontier model to reach Qed — because the four
that close need no model at all, and the loop class is blocked one layer past synthesis, in the
discharge tactic. But the frontier model was **necessary and sufficient to clear the
invariant-synthesis wall** that the local `qwen3-coder:30b` could not (local: 0 type-checking
loop invariants across 12 full-budget attempts; Opus: correct invariants, with non-trivial timing
reasoning). The honest one-liner: *the AI agent now does the creative half (the invariant); the
remaining gap is mechanical proof engineering on the loop closer, not model capability.*

### Caveat on these runs

This experiment was driven through a thin harness (`eval`-style script) that supplies the
skeleton + manifest config to the existing orchestrator; the only repo source change is the
`models.py` temperature fix. Permanently wiring skeleton synthesis + per-exit exact
postconditions + the witness-ordering fix into `pipeline.py`/`solve_timing_loop` is the
follow-up needed to make `cloq-agent prove-c <loop>` close end-to-end.
