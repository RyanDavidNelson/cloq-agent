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
- **Tasks 1–3 DONE.** The theorem builder is **generalized** (Requires/program/exits/entry-regs
  driven from `TargetSpec`; per-param register bindings `("x","N","R_A0")`); `cli.py` has the model
  preflight (`doctor`, and `prove` healthchecks before any synthesis run); `lift/cfg.py` emits an
  invariant *skeleton* (`skeleton_plan`) and `synthesize` has a `skeleton` mode that fills only the
  loop/entry holes (config default `synthesis_mode: skeleton`). `addloop_llm` exists as the
  synthesis twin of addloop.
- **Task A DONE (first constant-time target).** `prove ct_swap` closes via the gold path
  (`llm_calls=0`) reusing the vendored `crypto/ct_swap/ct_swap_proof.v` functor; `ct_swap_llm` is
  the synthesis twin. The vendored `ct_swap(secret a0,*a a1,*b a2,len a3)` is an array swap whose
  genuine secret (the a0 mask) **never appears in the timing invariant by design** — timing is a
  closed form in `len`/`index` only. So `secret_param: base_addr_b` (the a2 data pointer): it
  appears in the invariant yet no `cycle_count` arm depends on it (address-independence, the
  structural obligation `spec_lint` checks). ct_swap uses **R_A2/R_A3**, not addloop's R_T0/R_T1.
- **Task B DONE (M2: FreeRTOS list.c "easy four").** Four gold WCET targets — `vListInitialise`,
  `vListInitialiseItem`, `vListInsertEnd`, `uxListRemove` — all close via the gold path
  (`llm_calls=0`), each reusing its vendored `FreeRTOS/list/<fn>.v` functor over the shared
  `RTOSDemo` binary. Three are straight-line (entry+exit arms, shared gold proof via a YAML
  anchor); `uxListRemove` has a branch (extra invariant point `0x80002460`) + memory-noverlap side
  conditions and a bespoke gold proof. `vListInsert` (the cyclic-list search loop) is deliberately
  EXCLUDED (separate stretch task). The four `<fn>_llm` synthesis twins form the eval group
  `list_easy_four`: run `cloq-agent eval list_easy_four` for the success-rate table.
  `theorem_builder` gained `extra_binders` (ABI registers the invariant ignores — e.g.
  vListInsertEnd's a1 pointer — become forall binders + register hyps, not invariant args).
- The earlier module-scope blocker (`The reference startof was not found`) is **fixed**: the
  generated theorem is stated *inside* a functor mirroring the vendored proof
  (`Module {thm}_Proof (cpu : RVCPUTimingBehavior). Module Inner := TimingProof cpu. Import Inner.`),
  so `startof`/`models`/`rvtypctx` are in scope. Preserve this scoping in any theorem-builder change.
- The `TargetSpec.name` bug is **fixed**: the generated file is now `Addloop_gen.v` (target key),
  not `Lifted_prog_gen.v`.

- **Synthesis pipeline upgrades (took `eval list_easy_four` 0/4 → 3/4).** Five changes:
  (1) the four list `_llm` twins run in **skeleton mode** (pinned exit arm `time_of_<fn> t`, CFG
  supplies addresses/scaffold, model fills only entry/join holes); (2) `theorem_builder`/synth
  **re-pin the freeform Definition signature** (`_force_signature`) so a spurious leading
  `(p:addr)` binder can't under-apply `(inv_name args)`; (3) the orchestrator **feeds the previous
  attempt's Rocq/lint error back** into the next `synthesize` call; (4) a **generic structured
  proof driver** (`hammer.try_structured`: `apply prove_invs` + base case + `destruct_inv` +
  `all: repeat step; hammer`) closes straight-line / single-branch goals with a correct invariant,
  no LLM tactic-repair — this is what closes the three (`closing=structured`); (5)
  `invariant_attempts` 4→12 (local LLM, cheap). The CFG also now reports the real **exit address**
  (a `ret`/`jalr` is a leader) and **branch-join** invariant points.
- `uxListRemove_llm` is still ❌: its `0x80002460` join arm needs the `noverlaps`/`getmem_noverlap`
  branch reasoning, which the generic structured driver doesn't do. Closing it needs either a
  noverlaps-aware structured candidate or LLM proof-repair that discovers the bespoke tactics.
- **First NONLINEAR target: `find_in_array`** (a linear-search loop; WCET ~ len). The list "easy
  four" are *linear* (constant cycle count), so the invariant is trivial and the LLM is barely
  exercised; a loop forces a real `cycle_count_of_trace t' = a5 * (loop body)` closed-form arm —
  the actual synthesis test. `prove find_in_array` closes via the gold path (`llm_calls=0`); its
  `_llm` twin + `addloop_llm` + `ct_swap_llm` form the eval group **`loop_easy`** (the nonlinear
  success-rate slice). `theorem_builder` gained `inv_args` (explicit invariant application list) so
  the vendored invariant's vestigial leading `(s : store)` arg is passed without binding it.

## Next tasks
- More constant-time / WCET targets following the two-phase pattern (gold baseline target, then a
  `<name>_llm` synthesis twin). Each new vendored program needs its `.vo` built in
  `docker/Dockerfile.rocq` and an `-I` line in `proofs/_CoqProject` (see the ct_swap / FreeRTOS
  entries). Group `_llm` twins under `groups:` in targets.yaml for an eval slice.
- **`eval loop_easy` (nonlinear slice) = 0/3 → 1/3.** Two improvements landed here:
  - **#1 Proof-skill reuse.** `try_structured` now also tries a *library of proven gold proof
    scripts* (collected from the registry via `load_proof_library`, passed through `prove` →
    `_discharge`). A synthesized invariant whose arm structure matches a solved target is
    discharged by reusing that target's script — no LLM tokens. This closes **`addloop_llm`**
    (its synthesized invariant matches addloop's gold; `closing=structured`). Scripts that don't
    fit fail fast in `run_script` and are skipped, so trying the whole library is safe. (A purely
    generic loop tactic was attempted first but is brittle on the `msub_nowrap`/`N_sub_distr`
    wrap algebra — script reuse is the robust path and is the project's intended skill-accumulation.)
  - **#2 Loop-arm synthesis prompt.** `SYSTEM_SKELETON` now spells out the loop-arm closed form
    (`pre + counter_reg * t_body`, fall-through branch constant) and the exact legal `t*` constant
    names — branches have BOTH `tt<op>`/`tf<op>`, never a bare `tbeq` (the `find_in_array_llm`
    failure mode). The entry-hole hint says the entry arm is normally just `cycle = 0`.
- **Remaining loop gaps:** `ct_swap_llm` / `find_in_array_llm` still miss the closed-form loop arm
  (harder loops; no matching library script). `uxListRemove_llm`'s `0x80002460` join needs the
  noverlaps branch proof.
- `vListInsert` (the cyclic-list search loop, ~15 expert-hours) — the loop stretch target.

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
