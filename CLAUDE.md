# CLAUDE.md

Guidance for Claude Code working in this repo. Read this before editing.

## Project
cloq-agent: agentic synthesis + machine-checking of **Cloq** timing proofs (WCET +
constant-time) over **Picinæ**-lifted RISC-V binaries, using a local LLM + retrieval,
validated against a NEORV32 softcore on an FPGA. Two containers via `docker/compose.yaml`:
`rocq` (pet-server on :8765) and `agent` (the Python orchestrator).

The load-bearing design fact: a Cloq timing proof's structure is **isomorphic to the
control-flow graph**, and `repeat step; psimpl; lia` (the hammer ladder) discharges the bulk
automatically. The only creative input is the **invariant set** — for each loop a closed-form
timing expression `(c0 − c)·t` plus a termination quantity. So the LLM's whole job is to
propose that invariant (and, as a fallback, repair tactics on residual goals). It is a
**generator**; petanque/Rocq is the **verifier**; the generator is never trusted — a wrong
guess yields a *failed* proof, not an unsound one.

## How to run
- Build/up proof engine: `docker compose -f docker/compose.yaml up -d --build rocq`
- Hand-built smoke build: `docker compose -f docker/compose.yaml exec rocq bash -lc 'eval $(opam env); cd /work/proofs && coq_makefile -f _CoqProject -o Makefile.coq && make -f Makefile.coq'`
- Agent end-to-end, **no LLM** (gold path): `docker compose -f docker/compose.yaml run --rm agent prove addloop`
- Agent end-to-end, **LLM synthesis** (once `addloop_llm` exists): `... run --rm agent prove addloop_llm`
- Build the RAG index (do this before any LLM run): `... run --rm agent index`
- Model server: Ollama on the host (`qwen3-coder:30b`), reached at `host.docker.internal:11434`.
  Already running, GPU-resident. The agent's LLM client lives in `src/cloq_agent/models.py`
  (`LLM(cfg.model)`), constructed in the orchestrator but only *called* off the non-gold path.

## Where the code lives
```
src/cloq_agent/
  proof/
    petanque_driver.py   thin typed wrapper over pytanque (start / run tactic / read goals)
    hammer.py            ordered tactic ladder tried BEFORE any LLM call (hammer, lia, sauto…)
    theorem_builder.py   TargetSpec + PROOF_TEMPLATE + render()/write() — assembles the .v
  rag/
    embeddings.py store.py index.py retriever.py   goal-state / CFG-description retrieval
  agent/
    invariant_synth.py   synthesize(llm, *, name, entry, cfg_description, retrieved, escalate)
    tactic_repair.py     LLM proposes ≤5 tactics for a stuck goal; orchestrator tries each
    orchestrator.py      Orchestrator.prove(...): the budgeted think/act loop + spec_lint
  lift/
    cfg.py               parse_objdump() + build_cfg(); CFG.describe() → prompt context
  models.py              LLM client (Ollama/vLLM, OpenAI-compatible HTTP)
  cli.py                 index | prove | eval
eval/
  targets.yaml           target registry (specs, objdump, gold_invariant/gold_proof, secrets)
  targets.py             build_spec(): yaml → (TargetSpec, cfg_description, secret, gold_inv, gold_proof)
tests/                   pytest; test_theorem_builder.py covers render()
vendor/picinae/          Picinæ + Cloq. READ-ONLY. Never edit; check its license before redistribution.
```

## The loop (orchestrator.py, `Orchestrator.prove`)
1. If `gold_invariant` is set and attempt==1 → use it (no LLM). Else
   `retriever.retrieve(cfg_description)` → `invariant_synth.synthesize(self.llm, …)` (`llm_calls += 1`).
2. `spec_lint` rejects vacuous claims (secret must appear in a constant-time invariant;
   `cycle_count` must be constrained).
3. `render(spec, invariant_src, …)` writes `proofs/targets/<Name>_gen.v`; `driver.start(...)`.
4. If the target also has a `gold_proof`, the deterministic smoke path runs that script verbatim.
   Otherwise `_discharge`: hammer ladder first; on residual goals, retrieve → `tactic_repair` →
   apply, budgeted, escalating the model after N tries.
5. On Qed (optionally vetoed by the FPGA oracle), the solved proof is written back into the RAG
   corpus (skill accumulation).

## Current state
- **M1 DONE.** `prove addloop` closes end-to-end with `llm_calls=0`. It reports `llm_calls=0`
  *by design*: addloop carries both a `gold_invariant` and a `gold_proof` in `targets.yaml`, so
  the orchestrator short-circuits the LLM and runs the gold script. This is the M1 exit criterion,
  not a bug.
- The earlier module-scope blocker (`The reference startof was not found`) is **fixed**: the
  generated theorem is stated *inside* a functor mirroring the vendored proof
  (`Module {thm}_Proof (cpu : RVCPUTimingBehavior). Module Inner := TimingProof cpu. Import Inner.`),
  so `startof`/`models`/`rvtypctx` are in scope. Preserve this scoping in any theorem-builder change.
- The `TargetSpec.name` bug is **fixed**: the generated file is now `Addloop_gen.v` (target key),
  not `Lifted_prog_gen.v`.

## Next tasks (see `docs/CLOQ_CODE_TASKS.md` for full, runnable specs)
1. **Get the LLM firing.** Add an `addloop_llm` target (copy of addloop, `gold_invariant`/`gold_proof`
   removed, `objdump:` kept) so the orchestrator takes the synthesis branch. Add a preflight that
   the `LLM` client can reach Ollama. Independent of task 2 — addloop_llm reuses the addloop program,
   so the (still addloop-specific) theorem builder already renders it.
2. **Generalize `theorem_builder`.** It is currently hardcoded to addloop (`Require Import
   riscv_addloop_timing_proof`, reuses `Program_addloop`/`lifted_prog`, fixed `R_T0`/`R_T1`
   entry hypotheses). Drive Requires/program/exits/entry-hypotheses from `TargetSpec` so a second,
   distinct program renders. This — not CFG work — is the real blocker for a non-addloop target.
3. **CFG → invariant skeleton.** Promote `lift/cfg.py` from emitting a prose `describe()` to also
   emitting an invariant *skeleton* (invariant-point addresses + `match … Some/None …` scaffold with
   holes), and have `synthesize` fill only the per-loop timing formula + termination quantity.
   Addresses come from the CFG, never the model; the postcondition stays pinned from the spec.

Recommended order: 1 → 2 → 3. Task 1 is the fastest way to see `llm_calls > 0`.

## Gotchas / key facts (still true)
- The vendored Cloq tactic is **`hammer`**, NOT `whammer` — that name does not exist in this
  vendored copy. (`docs/SPEC.md`/`README.md` still say `whammer`; the code/ladder uses `hammer`.)
  Drive automation through `proof/hammer.py`, not by hardcoding a tactic name.
- `_CoqProject` uses `-R ../vendor/picinae Picinae` plus `-I` for the riscv/examples/array dirs.
- addloop real lifted addresses: **0x8 entry / 0x20 exit** (not 0x0/0x10). `destruct_inv 32` in the
  gold proof is the 0x20 exit.
- Container mounts: `..:/app` and `../proofs:/work/proofs`; `workspace=/work/proofs`
  (`CLOQ_PETANQUE_WORKSPACE`). `src` is bind-mounted, so Python edits are live (clear `__pycache__`
  if stale). The generated `.v` is regenerated every run.
- Soundness rule for any change touching the theorem/invariant: the model may fill **invariant
  arms only**; the **postcondition is pinned from the trusted spec** and addresses/match structure
  come from the CFG. Never let model output widen or weaken the claim.

## Conventions
- Reuse the proof-engine stack wholesale; write only glue. Don't reimplement petanque, hammer, RAG.
- Hammer-first, LLM-fallback. Never call the model where the ladder would close the goal.
- Every solved proof goes back into the RAG corpus. Keep that write-back intact.
- Keep budgets (invariant attempts, repair iterations, tokens) — they bound cost and runaway loops.
- Don't edit `vendor/picinae/`. If you need a vendored definition, instantiate/import it.
- After any change: `prove addloop` must still close end-to-end, and `pytest tests/` must pass.
