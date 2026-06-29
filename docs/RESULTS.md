# cloq-agent — capabilities, results, and the ceiling

This is the consolidated milestone write-up: what the agent can do today, measured, and a
precise, evidence-backed account of where the approach stops working. For the working guidance
see `CLAUDE.md`; for the design see `docs/SPEC.md` / `docs/ARCHITECTURE.md`.

## One-line summary

The agent reliably synthesizes-and-proves **straight-line** WCET/constant-time timing theorems,
handles the **simplest counter loop**, and **cannot** yet do **data-structure loops** — those need
bespoke interactive-theorem-proving (decidability case-splits, memory-aliasing lemmas) that is not
invariant synthesis and does not generalize mechanically.

So the user's shorthand — *"good at linear, okay at basic loops, bad at data structures"* — is
accurate, with one refinement: for loops, the **timing math is solved for all of them** (derived
from the CFG, see below); what's missing is the **proof discharge**, which currently only lands a
pure counter loop.

## Measured results (current)

Two phases per target: a **gold** baseline (invariant + proof extracted from the vendored Cloq
proof; `llm_calls=0`) proves the template renders and the program is provable; a **`_llm`** twin
(gold removed) forces synthesis and is the measured number.

| slice | targets | gold | synthesis (`_llm`) |
|---|---|---|---|
| straight-line WCET (`list_easy_four`) | vListInitialise, vListInitialiseItem, vListInsertEnd, uxListRemove | 4/4 ✅ | **3/4** (uxListRemove ❌) |
| loops (`loop_easy`) | addloop, ct_swap, find_in_array | 3/3 ✅ | **1/3** (only addloop ✅) |
| constant-time | ct_swap | ✅ | ❌ (loop) |

`pytest tests/` → 27 passed. Run the slices with `cloq-agent eval list_easy_four` /
`cloq-agent eval loop_easy`.

Caveat on the numbers: the `_llm` targets are *twins of golds we hold*, and addloop_llm closes by
reusing addloop's own gold proof — so these are **in-distribution dev metrics (recall-leaning)**,
not a generalization claim.

### Held-out generalization (the number that was missing)

`docs/results/transfer.md` reports the first **held-out** number, on 20 functions reduced from
pinned OpenSSL 3.4.0 + FreeRTOS-Kernel V11.1.0 (`eval/transfer/`, run via
`python eval/transfer/run_transfer.py`). Each target's gold invariant/proof is withheld from the
proof library and the few-shot, so a pass is generalization, not recall; straight-line targets are
machine-checked to **Qed** with a CFG-derived deterministic proof.

- **Easy tier: 10/10 proved** held-out (branchless straight-line — real OpenSSL `constant_time_*`
  incl. `constant_time_lt`, and FreeRTOS `vListInitialise`/`vListInsertEnd`/`xTaskGetCurrentTaskHandle`/
  `xTaskGetTickCount`). WCET targets are proven as a **sound upper bound** (`cycle ≤ Σ`), CT targets
  as **exact** (`cycle = Σ`, the constant-time obligation).
- **Medium/hard: 0/10** — by design they hit the documented wall: array/pointer loops
  (`CRYPTO_memcmp`, `OPENSSL_cleanse`, `BN_consttime_swap`), an unsupported cyclic-list search
  (`vListInsert`), and a memory-aliasing branch (`uxListRemove`). 6 are *reduction-pending* (drag in
  full FreeRTOSConfig / a configured OpenSSL tree) and recorded as lift gaps.

That distribution — easy all pass, medium/hard at a named ceiling class — is the transfer finding.

**Skeleton work landed (toward medium/hard).** The synthesis skeleton (`lift/cfg.py`) now: (1) emits
the WCET claim as `≤` (path-aware sound bound) and CT as `=`; (2) detects the loop induction variable
+ step and emits an `exists i, (s R_X)=base+i·step` template for array/pointer loops; (3) emits a
`decide`-case-split scaffold for data-dependent (search) exits; (4) emits `noverlaps`/`getmem_noverlap`
obligations for aliased stores; (5) places cut-points at every invariant point. These shape the goal
for the synthesis agent — but *closing* array/search/aliasing loops still needs that agent + tactics
(a generic discharge does not close them; see the closer experiments), so the deterministic held-out
number stays at the easy tier.

## Capability matrix

| class | example | status | why |
|---|---|---|---|
| **straight-line** (constant cycle count) | vListInitialise* | **good (3/3)** | invariant is trivial (entry `cycle=0` + pinned exit); generic `try_structured` driver discharges it |
| **pure counter loop** | addloop | **okay (1/1, via recall)** | timing derived exactly; discharged by reusing addloop's gold script |
| **array/pointer loop (no early exit)** | ct_swap | **no** | needs an `exists`-index invariant + `handle_ex; exists (1+i)` witness; synthesis emits a pointer-diff index and no generic discharge supplies the witness |
| **search loop (data-dependent early exit)** | find_in_array, find_in_list | **no** | WCET-of-search proves a *found/not-found disjunction*, which forces a program-specific decidability case-split (`key_in_array_dec`, `key_in_linked_list_dec`) |
| **memory-aliasing branch** | uxListRemove | **no** | needs `noverlaps`/`getmem_noverlap` aliasing reasoning |

## What made the working parts work (methodology)

- **Functor-scoped theorem** — the generated theorem is stated inside a functor that instantiates
  the vendored CPU/timing modules, so `startof`/`models`/`lifted_prog`/the `t*` constants are in
  scope (`proof/theorem_builder.py`).
- **Generalized `TargetSpec`** — requires/program/exits/entry-registers, plus `extra_binders`
  (ABI regs the invariant ignores) and `inv_args` (e.g. a vestigial `(s:store)` arg) are spec-driven.
- **Skeleton synthesis** — the CFG pins invariant-point addresses + the match scaffold + the exit
  arm; the model fills only the holes (`lift/cfg.py:skeleton_plan`, `agent/invariant_synth.py`).
- **CFG-derived loop timing** — `cfg.loop_timing(header)` *sums* the per-instruction constants over
  the natural loop body and the straight-line prefix (`mnemonic → t<op>`, shifts → `tslli n`,
  branches → `tt/tf<op>` by which edge stays in the loop). Unit-tested to reproduce the vendored
  gold timing **exactly** for addloop / ct_swap / find_in_array. The model no longer guesses the
  timing terms — it supplies only the counter + data facts.
- **`try_structured` discharge** — the generic Cloq proof skeleton (`apply prove_invs` + base case
  + `destruct_inv` + `step; hammer`), then a **library of proven gold proof scripts**
  (`load_proof_library`): a synthesized invariant whose arm structure matches a solved target is
  discharged by reusing that script, no LLM tokens.
- **Verifier-guided refinement** — on a failed discharge, the unproven goal (the `cycle = …`
  mismatch, which is verifier output, not the spec answer — sound) is fed back into the next
  synthesis attempt.
- **Anti-vacuity** — `spec_lint` rejects a constant-time claim whose invariant doesn't mention the
  secret or doesn't constrain `cycle_count`.
- **Measurement: the synthesis/discharge ablation** — `prove <t> --ablate-gold-proof <gold>`
  synthesizes `<t>`'s invariant but discharges with `<gold>`'s gold proof, isolating "is the
  invariant correct?" from "can we discharge it?".

## The ceiling — where "mechanical proof + invariant synthesis" ends (with evidence)

The project's load-bearing thesis is *"the proof is mechanical (`step; hammer`); the only creative
input is the invariant."* That holds for **straight-line and pure counter loops** and **breaks for
data-structure loops**. The ablation pinned the wall precisely:

- ct_swap_llm / find_in_array_llm now **reach** the gold-proof discharge (the invariant typechecks,
  timing is right) but still fail on **structural exactness** — the synthesized fact conjuncts don't
  match the rigid positional `destruct PRE as (a & b & …)`, and find_in_array's CFG-cut `0x204` join
  arm changes `destruct_inv`'s goal count.
- Surveying the corpus, **every** vendored search loop (find_in_array, find_in_array_opt,
  find_in_list) needs a program-specific decidability lemma (`key_in_…_dec`) + case analysis,
  because proving an exact WCET over an early-exit loop requires deciding the data-dependent branch.
  ~~This is bespoke ITP, not genericizable~~ — **superseded by Phase 2 (below)**: for the *array*
  search loops the decidability lemma is a template parameterized by the recovered element address
  (`arr + (i << 2)` vs `arr + 4 * i`); find_in_list (linked list) and the cyclic vListInsert are the
  ones that genuinely stay bespoke.

Conclusion: further synthesis/discharge micro-optimization on this corpus is not load-bearing. The
next real capability requires a different tool (below), not another prompt or tactic tweak.

## Phase 0 — per-arm gold-proof replay (discharge oracle)

A ground-truth oracle that removes synthesis from the loop: state the theorem with the **gold**
invariant (from the registry, no LLM) and replay each `gold_proof` arm against the scaffold the
engine generates today, reporting per-arm goal deltas and the first failing closer
(`eval/replay.py`; `cloq-agent replay [target]`; `tests/test_replay_harness.py`).

Measured (live pet-server): **all three** gold-proof targets close arm-by-arm against the current
scaffold — addloop 12/12, **ct_swap 11/11** (array/pointer), **find_in_array 13/13** (search
early-exit), each reaching Qed including the `exists`-witness and `key_in_array_dec` case-split
arms. So for these two ceiling classes the **discharge layer is already sufficient given the right
invariant**; the wall is invariant *synthesis* (producing the exists-index / decidability-shaped
invariant the existing closers consume), not the closers. This is the substrate for developing the
next rung: write the arms, `replay`, read off exactly which arm/goal breaks — decoupled from
synthesis.

To stop the engine churning a known ceiling class through the full budget, `prove-c`/`prove-mc`
now **fail fast** with the structured diagnostic for a ceiling-classified target; attempting one
anyway requires `--force-synthesis`, which runs under a clamped budget
(`agent.ceiling_invariant_attempts` / `ceiling_search_max_runs`).

## Phase 1 — discharge robustness (array/pointer, ct_swap)

Phase 0 showed the gold *scripts* close, but they are positional (`destruct PRE as (a & b & …)`)
and so desync on a *synthesized* invariant whose conjuncts are reordered or whose arm count shifts.
Three discharge bugs, three fixes, all in the one generic `solve_timing_loop` tactic
(`lift/intake.py`), now tried BEFORE the positional gold library at the search root
(`agent/orchestrator.py`):

1. **Brittle positional destructuring** -> shape-based `destruct` (`match goal with H : _ /\ _ =>
   destruct H | H : exists _,_ => destruct H`), order/count agnostic.
2. **Deferred `eexists` never unifies** (the witness must precede the splits that constrain the
   cycle count) -> explicit witness: `handle_ex; exists (1 + i)` for the step, `exists 0` for the
   base, where `i` is found by its `i <= len` bound, not by position.
3. **CFG cuts change the subgoal count** -> one uniform `all: solve_timing_loop` over every
   `destruct_inv` arm, plus the trichotomy fact inlined (`assert (i = n \/ i < n) by lia`).

Result (live pet-server, no LLM): one unified tactic closes **both** addloop (counter loop) and
**ct_swap (array/pointer)** to Qed given the gold invariant — the first past-ceiling discharge.
`tests/test_discharge_robustness.py` pins both; `tests/test_replay_harness.py` still green.

**Non-vacuity (mutation, proof-only — FPGA parked).** `eval/mutate.py` now degrades to proof-only
(`caught` = the proof broke; FPGA variance optional) and gains `cycle_form_mutations`, which
corrupts the invariant's `cycle_count_of_trace t' = …` closed form (double/drop a per-instruction
term). `tests/test_mutate.py::test_ct_swap_close_is_non_vacuous`: the gold cycle form closes and
**every** corruption fails to discharge — so the array/pointer close is constrained by the real
timing, not vacuously true.

The remaining ct_swap gap is **synthesis** producing that `exists`-index invariant; discharge no
longer blocks it.

## Phase 2 — array-search decidability as a TEMPLATE (find_in_array)

The Phase-1 ablation called `key_in_array_dec` "bespoke, not genericizable." It isn't: find_in_array
and find_in_array_opt carry the *same* lemma differing only in the element address — `arr + (i << 2)`
vs `arr + 4 * i`. So it is a template parameterized by the recovered array shape (base register,
index register, element width, shift-vs-mul form).

Built (`lift/search_template.py`, `lift/cfg.py:array_search_shape`):
- **shape recovery** — from the loop body `slli off, idx, k ; add ea, base, off ; lw v, 0(ea)`,
  recover `ArrayShape(base, index, elem_bytes, shift_form)` (unit-tested on the find_in_array
  objdump -> base R_A0, index R_A5, 4 bytes, shift form);
- **emitter** — `key_in_array`, the index trichotomy `lt_impl_lt_or_eq`, the decidability
  `key_in_array_dec`, the found/not-found **disjunction** `timing_postcondition`, and the case-split
  `destruct (key_in_array_dec …) as [IN | NOT_IN]`, all specialised to the shape;
- **verified (live pet-server)**: the emitted `key_in_array_dec` type-checks for BOTH address forms
  (`tests/test_search_template.py`) — the genericity claim, proven, not asserted;
- the search-loop **synthesis hint** now names the concrete emitted predicate/case-split for the
  recovered shape.

**End-to-end (the loop closed).** `theorem_builder` gained a `search_defs` injection slot; `build_spec`
emits the template into the scaffold (namespaced `cloq_`) and rewrites the reused invariant + proof to
those names, so the proof's case-split runs on the EMITTED decidability, not the vendored copy. The
`find_in_array_tmpl` target proves this: it reaches **Qed 13/13** driving
`destruct (cloq_key_in_array_dec …) as [IN | NOT_IN]` and the two branches, with the vendored
`key_in_array_dec` / `timing_postcondition` namespaced away
(`tests/test_search_template.py::test_find_in_array_tmpl_closes_with_emitted_decidability`). This is the
find_in_array analogue of the ct_swap Phase-1 win: the decidability scaffold is machine-emitted from the
recovered shape and *drives the proof to Qed*.

Honest scope: the lifted **program** + the timing closed form `time_of_find_in_array` are still reused
from the vendored functor (the CFG-derived parts); and the two branch leaf scripts are still the gold
proof (renamed), not yet a uniform generic closer. **find_in_list** (needs list theory) and the cyclic
**vListInsert** (uniqueness-in-a-cycle) stay genuinely bespoke — not promised.

### Held-out measurement (the twin-vs-generalization line)

`find_in_array_tmpl` is an identical-program twin (reused program + reused `time_of_*` + renamed gold
leaves): it validates the emission MECHANISM, not generalization. To measure the gap, two fresh
search functions the corpus has never seen — `eval/heldout/se_find_eq.c` (`==`) and `se_find_ge.c`
(`>=`, a different predicate) — were run through the real `compile -> lift` front (gcc -O2, the pinned
flags; `eval/heldout/measure.py`). Findings:

- **program-half READY** — `intake.generate_scaffold` emits a `Program_<func>` functor for both; both
  classify correctly as `search early-exit`.
- **GAP 1 (upstream, newly revealed): shape recovery is itself twin-fragile.** gcc -O2 strength-reduces
  `arr[i]` to a **running pointer** (`lw a3,0(a5)` ; `addi a5,a5,4`) — there is no `slli`/`add`, so
  `cfg.array_search_shape` returns `None`. The vendored `find_in_array.objdump` carries the explicit
  `slli;add;lw` index form; a real compile does not. Until recovery handles the running-pointer form
  (base-pointer recovery, as in ct_swap's induction pointer), no template emits for held-out search.
- **GAP 2 (confirmed): no disjunctive timing.** `cfg.loop_timing` returns a single `(prefix, body)`,
  but `time_of_find_in_array` is a found/not-found **disjunction** (the partial-iteration cost differs
  on the two exit edges: `ttbgeu` vs `tfbgeu + … + ttbeq`). A held-out function can't reuse `time_of_*`;
  the CFG must emit the two-arm form. This is upstream of any closer — a wrong `time_of` closes nothing.
- **GAP 3: the generic branch closer** (still the renamed gold leaves).

Corrected order to "Phase 2 done to held-out": **(1) running-pointer shape recovery -> (2) generated
disjunctive timing -> (3) uniform branch closer -> one held-out search function at Qed** (se_find_eq
minimum, se_find_ge to prove the predicate generalized). The program-half is already wired.

## Next (see CLAUDE.md "Next tasks")

The honest path to data-structure loops is an **LLM proof-search agent** — a multi-step,
backtracking prover that can *discover* tactics like `destruct (key_in_array_dec …)` and the
case-specific reasoning — and a **held-out generalization target** (a loop whose gold is withheld
from the library/few-shot) so the metric measures capability, not recall.
