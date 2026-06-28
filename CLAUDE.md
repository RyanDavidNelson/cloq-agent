# CLAUDE.md

## Project
cloq-agent: agentic synthesis + machine-checking of Cloq timing proofs over
Picinae-lifted RISC-V binaries. Two containers via `docker/compose.yaml`:
`rocq` (pet-server on :8765) and `agent` (the Python orchestrator).

## How to run
- Build/up proof engine: `docker compose -f docker/compose.yaml up -d --build rocq`
- Smoke proof (hand-built): `docker compose -f docker/compose.yaml exec rocq bash -lc 'eval $(opam env); cd /work/proofs && coq_makefile -f _CoqProject -o Makefile.coq && make -f Makefile.coq'`
- Agent end-to-end: `docker compose -f docker/compose.yaml run --rm agent prove addloop`
- Model server: Ollama on the host (qwen3-coder:30b), via `host.docker.internal:11434`. Already running, GPU-resident.
- Tests: `docker compose -f docker/compose.yaml run --rm agent pytest -q`

## Current task (where we are)
DONE: the discharge loop is now a **DFS-with-backtracking proof search over petanque states**
(replaced the greedy single-goal repair that couldn't handle `destruct_inv` / branch fan-out —
the dominant failure mode on the FreeRTOS list set, ct-swap, chacha20). All four coordinated
edits landed (per-tactic timeout, theorem_builder prelude/scope + filename fix, ladder
workhorses, DFS `_discharge`), plus tactic_repair past-failures/splitter-bias and RAG indexing
of vendored example proof bodies. Verified: `pytest -q` green (incl. mock-driver backtracking
tests + skippable real-petanque integration tests), `prove addloop` gold path closes with
llm_calls==0, and the search reaches addloop's 2-arm `destruct_inv` fan-out against pet-server.

DFS+LLM eval (qwen3-coder:30b, skeleton synthesis): 5/7 `_llm` targets PROVED — the straight-line
/ single-branch list ops (`vListInitialise/Item/InsertEnd`, `uxListRemove` incl. its 2-arm
`destruct_inv`) and `addloop_llm` (via proof-library reuse), all closed at the ROOT by the
deterministic structured prelude with ZERO tactic-repair calls (LLM's only input = the invariant).
2 FAILED — diagnosed by probing the generated `.v`:
- `find_in_array_llm`: invariant type-checks and reaches the fan-out, but the model put a
  FUNCTIONAL-CORRECTNESS conjunct in it (`forall i, mem[arr+(i<<2)] <> key`) that no timing ladder
  can discharge → an invariant-SYNTHESIS scope problem, not a closer gap.
- `ct_swap_llm`: memory-aliasing residuals; the DFS makes ~17 repair calls/attempt (now visible
  after the llm_calls accounting fix) but the LLM doesn't assemble the `preserve_noverlaps` /
  `getmem_noverlap` sequence.

KEY ABLATION (clean, equal 12-attempt budget, `agent.llm_repair_enabled` on/off): deterministic-only
== full DFS+LLM == **5/5** on every solvable target, each `llm=1` (synthesis only, ZERO repair).
So the **DFS+LLM tactic-repair layer is not the deciding factor on any current target** — the
deterministic scaffold (structured prelude + proof library) + a correct invariant does the work. The
backtracking search is correct and ready (mock-tested) but its regime — a fan-out the deterministic
ladder can't close but the LLM can — is not yet exercised by a real target. The 2 failures fail in
BOTH modes (`find_in_array_llm`: synthesized invariant MISSING the required memory-frame predicate;
`ct_swap_llm`: memory aliasing). (Earlier "1/7 deterministic-only" was an artifact of a buggy lint,
since removed — see below.)

Follow-ups DONE:
- **Ablation switch** — `agent.llm_repair_enabled` (default true; false = deterministic layer only).
  This is what produced the finding above. KEEP.
- **llm_calls accounting** — `prove()` carries `_discharge`'s repair calls into the final result
  (was under-reporting failed runs as synthesis-only). Confirmed live (ct_swap 12→37).
- **Loop-arithmetic ladder** — `hammer.py` `_LOOP_ARM` (unpack PRE + step + `msub_nowrap`/
  `N_sub_distr`, all GLOBAL tactics) as two LADDER rungs + a STRUCTURED_SCRIPT. Functional, but has
  not yet flipped a target (addloop_llm closes via library reuse). Kept as a grounded tool.
- **Frame-prompt** — `SYSTEM_SKELETON` now demands the memory-frame predicate for memory-loop
  targets (the real find_in_array gap) + a loop-counter worked exemplar in `tactic_repair.SYSTEM`.
- **Escalation via env** — `CLOQ_ESCALATION_BASE_URL`/`_NAME`/`_API_KEY` (key falls back to
  `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`); no secret in any committed file. Public repo.

DEAD ENDS (tried, reverted — do not re-attempt without new info):
- **Per-arm `cycle_count` lint**: rejects the EXIT arm, which references a named postcondition
  predicate (`time_of_X t ...`) whose cycle equation lives elsewhere → false-rejected 5/6 gold
  invariants. Removed. (The "ban functional-correctness conjuncts" idea was also wrong: the gold
  find_in_array invariant legitimately uses `forall i, mem[...] <> key`.)
- **Deterministic noverlap closer**: `preserve_noverlaps`/`unfold_noverlap` are program-specific
  `Local Ltac` (defined inside each vendored proof body), out of scope in our generated theorem, so
  any such rung is a try-skipped no-op. Removed. REAL fix = `theorem_builder` must EMIT a
  program-specific `unfold_noverlap`/`preserve_noverlaps` from the target's memory structure.

NEXT (open, re-prioritized by the ablation):
- **The deterministic scaffold + synthesis is what wins** — invest there: get the synthesized
  invariant to include the memory-frame predicate (find_in_array) and emit program-specific
  noverlap-unfold tactics in `theorem_builder` (ct_swap). Both are deterministic-layer fixes.
- **Find/build a target that actually needs the DFS+LLM repair** (fan-out the ladder can't close but
  the LLM can) — otherwise the search layer, though correct, is unexercised complexity.
- **Best-first upgrade (Task 8, optional):** `agent.search_strategy` flag swapping the DFS stack
  for a priority queue keyed by open-goal count; keep DFS the default/tested path.
- The `whammer` mention in docs/ARCHITECTURE.md "Automation" is stale (vendored closer is
  `hammer`); fix when next touching that file.

The change set is four coordinated edits, in dependency order — ALL DONE:
1. **Driver timeout** (DONE) — per-tactic timeout in `PetanqueDriver.run` (`agent.tactic_timeout_s`,
   native int seconds + client thread guard); a hung `repeat step`/`psimpl` is a skipped rung.
2. **theorem_builder scope + prelude** (DONE) — `render(proof_body=None)` emits the functor-scoped
   theorem + `PRELUDE_LINES` (`Local Ltac step` … `destruct_inv {addr_width} PRE`) as an OPEN proof;
   `start` succeeds and reaches the fan-out. Filename bug fixed.
3. **Ladder** (DONE) — `hammer.py LADDER` carries `now step.` + the `repeat step; …` workhorses
   ahead of the generic closers; each rung bounded by the per-tactic timeout.
4. **DFS `_discharge`** (DONE) — `Orchestrator._discharge` is now a stack-based backtracking search
   over petanque states (`_SearchNode` = state + tactic-path + depth; `_RunCounter` bounds total
   `driver.run`; `visited` by `_goal_hash`). A node is solved iff `state.finished` (petanque holds
   the multi-subgoal conjunction). Hammer-first at every node; the deterministic structural prelude
   + proof-library run at the ROOT (advancing to the post-`destruct_inv` fan-out), LLM repair with
   per-goal `past_failures` deeper. Budgets: `search_max_depth`/`search_max_runs`/`max_iterations`
   (LLM-call cap). Stale handles fall back to `replay_from_root`. `prove()` + the gold path are
   unchanged (gold returns before `_discharge`).

See `docs/cloq-agent-backtracking-tasks.md` for the copy-pasteable Claude Code task series.

Smoke-path prereqs — RESOLVED (Task 3):
- `driver.start` now succeeds on the generated `.v` (the "startof not found" scope error is
  gone): `render(proof_body=None)` emits the functor-scoped theorem + the deterministic
  Picinae prelude, and probing confirms `start` → prelude → `destruct_inv 32 PRE` reaches
  2 subgoals for addloop. The prelude lives in `theorem_builder.PRELUDE_LINES` (open proof:
  no Qed, no functor `End` — an unfinished proof can't be followed by `End ..._Proof.`; the
  search drives it to Qed). A closed `proof_body=<script ending in Qed.>` re-enables the
  functor `End` + concrete-CPU instantiation suffix.
- Filename bug fixed: `build_spec(name=...)` is now required and no longer falls back to
  `lifted_program`, so addloop renders to `Addloop_gen.v` (was `Lifted_prog_gen.v`).

## Architecture decision: backtracking proof search
- **Why DFS+backtracking, not greedy repair.** The old `_discharge` reads only `cur.goals[0]`,
  commits to the first tactic that returns `ok` (which only means "applied without error",
  not "made progress"), discards the prior state, and abandons the whole proof on any dead
  end. `destruct_inv` always applies cleanly, so it is always taken, the pre-split state is
  lost, and there is no way back. Closest published analogue is COPRA (stack-based
  backtracking search + retrieval + a past-failure dictionary); backtracking's payoff is
  largest on the harder, branching proofs — exactly our regime.
- **A search node = a petanque state.** The state carries the full goal stack, so the
  multi-subgoal fan-out is handled for free: success is `state.finished`; a `destruct_inv`
  just yields a child with more open goals.
- **Picinae tactics are opaque to the search.** The searcher never interprets `destruct_inv`
  or `preserve_noverlaps`; it runs the string and lets Rocq adjudicate. Domain-specificity
  lives in (a) the deterministic prelude, (b) the ladder, (c) RAG over vendored proofs — not
  in the search algorithm.
- **Upgrade path (later):** swap the DFS stack for a best-first priority queue once a cheap
  value signal exists (subgoal-count delta, or "ladder closed ≥1 subgoal"). Keep DFS default.

## Picinae tactic vocabulary (what the proofs actually require)
Grounded in `vendor/picinae/timing/examples/FreeRTOS/list/*.v`. Tiers:
- **T1 — structural prelude (identical every proof, deterministic, NOT LLM-discovered):**
  `apply prove_invs`; the setup block (`eapply startof_prefix in ENTRY`;
  `eapply preservation_exec_prog in MDL … apply lift_riscv_welltyped`; `clear -` + renames);
  then `destruct_inv W PRE` (W = address width, 32 for RV32). `destruct_inv` is THE
  case-split — one subgoal per invariant program point.
- **T2 — workhorse:** `Local Ltac step := tstep r5_step.` (step is rebound per proof!), then
  `repeat step; psimpl; hammer` (morally `whammer`). `step` is undefined without the binding.
- **T3 — per-branch fan-out:** entry closes with `now step`; straight segments with
  `repeat step. hammer.`; branch points open taken/not-taken subgoals (a `BC` hypothesis
  appears) discharged in `{ … }` focus blocks; vanilla `destruct PRE as (...)` unpacks the
  invariant conjunction.
- **T4 — memory-aliasing residual closers (enter via RAG over vendored proofs):**
  `preserve_noverlaps`, `unfold_noverlap`, `unfold_create_noverlaps`, `getmem_noverlap`,
  `noverlap_symmetry`, `find_rewrites`, then `lia`.

A stock Coq agent is helpless here: the load-bearing moves are Picinae-specific and `step`
is bound at proof scope.

## petanque state semantics (resolves the "many live states?" question)
- `run(state, tac)` returns a NEW state; input handle is untouched; on Rocq error it returns
  the SAME input state with `ok=False` (so a failed candidate naturally yields the parent).
- Open question: does the server keep OLD states runnable after newer ones are produced?
  Unconfirmed from source. **Resolution: store per node both the `state` handle AND the
  tactic-path from root.** Try the handle; if it errors as stale, `replay_from_root(path)`
  (fresh `start` + replay). Task 1 probes which mode the pinned pytanque supports.
- **RESOLVED (Task 1, probed empirically): HANDLES STAY LIVE.** An OLD state handle is still
  runnable after NEWER states are produced from the same parent in the same session
  (`run(s0,_)->s1`, `run(s0,_)->s1b`, then `run(s1,_)` still `ok=True`). The DFS search MAY
  therefore hold many live handles and does not need to replay by default.
  `PetanqueDriver.replay_from_root(file, theorem, path)` exists as a defensive fallback for
  the case a handle is ever rejected as stale. Probe lives as a skipped integration test in
  `tests/test_petanque_state.py` (needs a running pet-server).

## Key facts learned
- `_CoqProject` uses `-R ../vendor/picinae Picinae` + `-I` for riscv/examples/array dirs.
- Vendored Cloq tactic is `hammer` (NOT `whammer` — that name doesn't exist here).
- addloop real addresses are 0x8 (entry) / 0x20 (exit), not 0x0/0x10 (the generated
  invariant currently uses the wrong ones).
- `theorem_builder.render` wraps the theorem in `Module {thm}_Proof (cpu …)` + `Module Inner
  := TimingProof cpu. Import Inner.` AND (Task 3) emits the per-proof `Local Ltac step` +
  deterministic Picinae prelude as the open proof body. Verified against pet-server: `start`
  succeeds and the prelude reaches `destruct_inv 32 PRE` (2 subgoals) for addloop. The old
  malformed `Admitted.`/`Qed.` default is gone (`proof_body=None` ⇒ open proof, no closer).
- `TargetSpec.name` filename bug FIXED (Task 3): `build_spec(name=...)` is required and no
  longer falls back to `lifted_program`; addloop → `Addloop_gen.v`.
- agent mounts: `..:/app` and `../proofs:/work/proofs`; workspace `=/work/proofs`
  (`CLOQ_PETANQUE_WORKSPACE`).
- src is bind-mounted, so Python edits are live (clear `__pycache__` if stale); `.v`
  regenerates each run.

## Conventions / gotchas
- Never spawn coqtop. Drive only through `petanque_driver.PetanqueDriver`.
- pytanque's native `run(..., timeout=)` is INTEGER seconds — a float raises Coq "This number
  is not an integer." The driver coerces (`max(1, int(round(budget)))`); the float budget only
  drives the client-side thread guard.
- Keep the gold-proof deterministic path (no LLM) working as the smoke regression.
- Hammer-first, always: deterministic ladder before any LLM call.
- Preserve `ProofResult` shape; record the winning tactic path in `proof_script`.
- Every loop is budgeted (depth, driver.run calls, LLM calls). No unbounded search.
- Don't break `pytest`; add tests with each change.
