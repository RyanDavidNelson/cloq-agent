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

- **Easy tier: 8/10 proved** held-out (branchless straight-line — incl. real OpenSSL `constant_time_*`
  and FreeRTOS `vListInitialise`/`vListInsertEnd`/`xTaskGetCurrentTaskHandle`). The non-passes are a
  degenerate identity body (`value_barrier` optimizes to a bare `ret`) and one *reduction-pending*.
- **Medium/hard: 0/10** — by design they hit the documented wall: array/pointer loops
  (`CRYPTO_memcmp`, `OPENSSL_cleanse`, `BN_consttime_swap`), an unsupported cyclic-list search
  (`vListInsert`), and a memory-aliasing branch (`uxListRemove`). 6 are *reduction-pending* (drag in
  full FreeRTOSConfig / a configured OpenSSL tree) and recorded as lift gaps.

That distribution — easy mostly pass, medium/hard at a named ceiling class — is the transfer finding.

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
  This is bespoke ITP, not genericizable, and there is **no remaining generic ("simple") loop** in
  this RISC-V corpus to gain. (The one clean accumulate loop, x86 `sum`, would need the AMD64 timing
  pipeline.)

Conclusion: further synthesis/discharge micro-optimization on this corpus is not load-bearing. The
next real capability requires a different tool (below), not another prompt or tactic tweak.

## Next (see CLAUDE.md "Next tasks")

The honest path to data-structure loops is an **LLM proof-search agent** — a multi-step,
backtracking prover that can *discover* tactics like `destruct (key_in_array_dec …)` and the
case-specific reasoning — and a **held-out generalization target** (a loop whose gold is withheld
from the library/few-shot) so the metric measures capability, not recall.
