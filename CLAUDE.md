# CLAUDE.md — cloq-agent

> Guidance for Claude Code working in this repo. Read this, then `docs/RESULTS.md`
> (what works + the ceiling; its "Next" section is the ordered plan).

## Project

`cloq-agent`: agentic synthesis + machine-checking of **Cloq** timing proofs (WCET +
constant-time) over **Picinæ**-lifted RISC-V binaries. The timing model is calibrated to the
**NEORV32** softcore. The LLM only *proposes*; Rocq *checks*. Nothing the model writes enters
the trusted artifact without passing the prover.

We are turning this CLI research prototype into a **cloneable, compose-up application** a lab
can run end-to-end: upload a C file -> pick MCU/arch/compiler (only RISC-V / NEORV32 today) ->
compile -> lift -> prove -> get **either** a proof with a cycle-count closed form + predicted
range **or** a structured failure diagnostic. Solved proofs/invariants are written back into
the RAG corpus (already implemented).

## Current state — READ THIS (see `docs/RESULTS.md` for evidence)

The proof engine is **well past bring-up**; the smoke target proves and the methodology is in
place. Do **not** redo any of this:

- `prove addloop` closes end-to-end, no LLM. The `startof` scope bug is **fixed** by a
  **functor-scoped theorem** (`proof/theorem_builder.py`).
- Built and working: generalized `TargetSpec` (`extra_binders`, `inv_args`); **CFG-derived
  loop timing** (`lift/cfg.py:loop_timing`, unit-tested to match vendored gold exactly);
  **skeleton synthesis** (model fills only the holes); **`try_structured` discharge** + a
  **gold-proof library** (`load_proof_library`); **verifier-guided refinement**; `spec_lint`
  anti-vacuity; the **synthesis/discharge ablation** (`prove <t> --ablate-gold-proof <gold>`).
- Proof-**search** agent (`AGENT.md`) exists and works: DFS **backtracking** over petanque states,
  `agent.tactic_timeout_s`, `search_max_depth`, `search_max_runs`, `driver.replay_from_root`.
- **Phase 0** (`eval/replay.py`, `cloq-agent replay`): per-arm gold-proof replay oracle — states a
  theorem with the GOLD invariant (no LLM) and replays each arm vs the generated scaffold, so
  discharge is testable in isolation from synthesis. Plus a ceiling **fail-fast gate** in
  `pipeline.py` (`--force-synthesis` + clamped budget).
- **Phase 1** (discharge robustness): `solve_timing_loop` is now order-agnostic (shape-based
  `destruct`), witness-explicit (`handle_ex; exists (1+i)` / `exists 0`, the index found by its
  `i <= len` bound — not deferred `eexists`), over one uniform `all:` dispatch, and is tried BEFORE
  the positional gold scripts. One unified tactic closes **both** the counter loop (addloop) and
  the **array/pointer loop (ct_swap)** with no LLM, given a correct invariant — validated
  non-vacuous by cycle-form mutation (`eval/mutate.py`, proof-only).
- **Phase 2** (`lift/search_template.py`, `lift/cfg.py:array_search_shape`, `theorem_builder`
  `search_defs` slot): the array-search **decidability is a TEMPLATE**, not bespoke — recover the
  element shape (`arr + (i << 2)` vs `arr + 4 * i`) and emit `key_in_array` / `key_in_array_dec` /
  the found/not-found disjunction / the `destruct (key_in_array_dec …)` case-split. Wired
  end-to-end: `find_in_array_tmpl` reaches **Qed 13/13** driving the EMITTED (`cloq_`-namespaced)
  decidability, vendored copy namespaced away — the find_in_array analogue of the ct_swap win.
  Remaining: the two branch leaf scripts are still the (renamed) gold proof, the lifted program +
  `time_of_find_in_array` are still reused, not generated.

**Measured (in-distribution / recall-leaning, NOT held-out):**
`cloq-agent eval list_easy_four` -> 3/4 synth (uxListRemove fails) - `eval loop_easy` -> 1/3 synth
(only addloop passes) - `pytest tests/` -> 125 passed / 5 skipped (in the agent container).

**The ceiling (the important part).** Discharge now closes **straight-line**, **pure counter loop**,
and **array/pointer loop** (ct_swap) GIVEN a correct invariant. Remaining gaps:
- array/pointer **end-to-end**: discharge is solved; the open part is **synthesis** emitting the
  `exists`-index invariant (the model's job, or a future deterministic array deriver);
- search loop w/ data-dependent early exit (`find_in_array`): the decidability case-split is now
  **templated, emitted, and proven end-to-end** (`find_in_array_tmpl` reaches Qed on the emitted
  defs, Phase 2); remaining = a uniform generic branch closer + generating (not reusing) the
  program/timing. `find_in_list` (list theory) and cyclic `vListInsert` (uniqueness-in-a-cycle)
  stay genuinely bespoke;
- memory-aliasing branch (`uxListRemove`) — needs `noverlaps`/`getmem_noverlap` reasoning.

## Deferred (out of scope for now)

- **FPGA validation.** Parked at the user's request. `fpga/` stays in the repo but is **off the
  critical path** — no board dependency in CI, the GUI, the report, or the transfer metric.
  Output is the proven **closed-form cycle count + predicted range** only; there is no
  measured-vs-predicted right now.
- **What replaces FPGA's implicit checks (no-FPGA integrity).** Two anti-vacuity gates stand in for
  the two things hardware used to cross-check: (1) `eval/mutate.py` (proof-only) — corrupt the cycle
  closed form, the proof MUST break (postcondition non-vacuity); (2) `proof/premise_check.py` — the
  **premise-satisfiability gate**, run on every `prove()`: emit `exists <binders>, <input premises>`
  and require Rocq to discharge it, so a contradictory premise (PTR_ALIGN/LEN_VALID/noverlaps) is
  rejected at generation time instead of yielding a vacuously-true theorem. The report states the
  **trust basis** plainly: proofs are sound *relative to* the NEORV32 timing model (now a fully
  trusted, hardware-unvalidated input) and the pinned flags; constant-time is **formal-only** (the
  secret provably never enters the closed form, `spec_lint`-enforced) — no empirical dudect check.

## Golden rules

1. **Do not break current functionality.** `prove addloop`, `eval list_easy_four`,
   `eval loop_easy`, and `pytest tests/` must stay green at every step.
2. **Generator-verifier discipline stays.** The GUI/API/new code never treat an LLM artifact as
   trusted. Soundness comes from Rocq, not from any parser, the GUI, or the LLM.
3. **No FPGA on the critical path.** Proof-only output (closed form + predicted range). Don't
   add board dependencies to build/run/CI.
4. **Pin everything that affects cycle counts.** Compiler, flags, NEORV32 commit/config. `-O`
   level changes instruction selection -> changes the timing model; flags and the timing model
   are a matched pair. Document any change.
5. **Secrets via env only.** API keys from env / `.env` (compose `env_file`). Never commit,
   bake into an image, or log a key.

## Repo layout (current + planned)

```
src/cloq_agent/
  proof/      petanque driver, hammer ladder, theorem_builder        [exists]
  rag/        embeddings, store, index, retriever                     [exists]
  agent/      orchestrator, invariant_synth, tactic_repair, search    [exists]
  lift/       cfg.py (objdump->CFG, loop_timing, skeleton_plan)       [exists]
  lift/       compile.py (C->ELF/obj via riscv32-gcc) + riscv_lifter  [ADD]
  report.py   ProofResult -> structured diagnostic (json + html/md)   [ADD]
  cli.py      index | prove | eval (+ `compile`, `prove-c`)           [extend]
api/          FastAPI service wrapping the orchestrator (SSE/WS)       [ADD]
gui/          frontend SPA (upload C, pick target, stream, render)    [ADD]
config/       default.yaml (+ local.yaml, api.yaml profiles)          [extend]
proofs/       Rocq targets + build (addloop smoke)                    [exists]
eval/         targets.yaml, harness, mutate, ablations                [exists]
eval/transfer/  OpenSSL + FreeRTOS held-out suite (20 targets)        [ADD]
docker/       Dockerfile.rocq, Dockerfile.agent, compose.yaml         [extend]
docker/       Dockerfile.toolchain, Dockerfile.gui                    [ADD]
.github/workflows/  ci.yml, build.yml, nightly.yml                    [ADD]
fpga/         NEORV32 oracle — DEFERRED, off critical path            [parked]
docs/         SPEC, ARCHITECTURE, RESULTS, BRINGUP, FILEMAP           [exists]
```

## How to run (current)

- Proof engine: `docker compose -f docker/compose.yaml up -d --build rocq`
- Agent end-to-end: `docker compose -f docker/compose.yaml run --rm agent prove addloop`
- Eval slices: `cloq-agent eval list_easy_four` - `cloq-agent eval loop_easy`
- Isolate synth vs discharge: `cloq-agent prove <t> --ablate-gold-proof <gold>`
- Model server: Ollama on host (`qwen3-coder:30b`) via `host.docker.internal:11434`.

## How it should run (target state)

- `cp .env.example .env` (fill `CLOQ_API_KEY` for the API profile).
- Local + escalate:  `docker compose --profile local up`  (Ollama bundled or on host)
- API-key only:      `docker compose --profile api up`    (no GPU/Ollama needed)
- GUI at `http://localhost:8080`: upload `.c`, pick NEORV32, run.
- `rag_store/` and `runs/` are named volumes so the corpus + outputs persist.

## Conventions

- Python: ruff-clean (`ruff check src eval api`), typed dataclasses, no new heavy deps without
  reason. Keep the "reuse the engine, write only glue" rule.
- New config keys: add to `config/default.yaml`, the typed dataclasses in `config.py`, and make
  them overridable via `CLOQ_<SECTION>_<KEY>` env vars (the existing pattern).
- Every new pipeline stage emits a structured record into `ProofResult`/the report so the GUI
  can show *which stage failed and why*: `compile | lift | spec-lint | invariant | repair |
  stored`. When a failure is a known ceiling class (array/pointer, search, aliasing loop), label
  it as such in the diagnostic so it reads as expected-limitation, not a crash.
- Tests: a pytest per new module; CI stays green. `rocq-smoke` is green now (addloop proves) —
  keep it that way; it is **not** `allow_failure`.

## A target is "properly configured" when it has

A compilable self-contained C unit + pinned flags, a lifted `.v` (program map + start/end
addrs), a `targets.yaml` entry (`entry_addr`, `exit_point`, `theorem_name`, `params`,
`description`, `objdump`, and for CT a `secret_param`), and a stated property (WCET or
constant-time). A gold cycle count exists only where the Cloq paper provides one; otherwise the
result is **proof-only** (closed form + predicted range). For **held-out** transfer targets,
withhold the gold invariant/proof from the library and few-shot so the metric measures
capability, not recall. Outputs land under `runs/transfer/<suite>/<target>/`.

See the "Next" section of `docs/RESULTS.md` for the ordered, acceptance-criteria'd plan.
