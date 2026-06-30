# Architecture

The design rule for this repo: **reuse the proof-engine stack wholesale, build only the glue.**
Below is what each layer is, why it's the right off-the-shelf choice, and where our code sits.

## Proof engine: coq-lsp + petanque + pytanque

We never spawn `coqtop` or talk to Rocq directly. The current, maintained, AI-oriented path is:

- **coq-lsp / rocq-lsp** (ejgallego; v0.2.5; built on the Flèche incremental engine) — the language
  server. It replaces the now-deprecated Coq SerAPI that older neural provers (CoqGym, PyCoq,
  Coq Serapy) depended on.
- **petanque** — a low-latency proof API that ships *inside* coq-lsp, exposed as `pet-server` on
  TCP 8765, explicitly designed for AI/agentic use (start a proof, run a tactic, read goals).
- **pytanque** (LLM4Rocq) — the Python client for petanque. This is the gym-like interface our
  orchestrator drives. `proof/petanque_driver.py` is a thin typed wrapper over it.
- **coqpyt** (sr-lab) — a higher-level coq-lsp client used by `rag/index.py` to mine lemma/AST
  data for retrieval (with a regex fallback when it isn't installed).
- **rocq-mcp** (LLM4Rocq) + upcoming **native MCP in coq-lsp** — optional: expose the agent's
  proof tools over MCP so a frontier model can drive it directly. We don't depend on it.

## Automation: hammer-first

`proof/hammer.py` is an ordered tactic ladder tried before any LLM call: Cloq's own
`whammer`/`hammer` (which already close most timing goals), then CoqHammer (`sauto`/`qauto`) and
`lia`. This "symbolic-first, LLM-fallback" ordering is the consistent winner in recent
agentic-Rocq systems, and it keeps token spend proportional to genuine difficulty.

## Retrieval: the highest-leverage glue

Per Rango (ICSE '25), retrieval is decisive (proof rate ~30% → ~18.6% without it) and retrieving
*both* prior lemmas *and* prior proofs matters most. So:

- `rag/index.py` builds two corpora: Picinæ/Cloq lemma & definition signatures, and completed
  proofs/invariant sets (seeded from `proofs/`, grown from `runs/`).
- `rag/retriever.py` queries by **goal state** (for repair) or **CFG description** (for invariant
  synthesis), returning lemmas and analogous proofs separately because they fill different prompt
  roles.
- `rag/store.py` is a transparent numpy/JSONL cosine store — swap in FAISS/Chroma later behind the
  same interface if the corpus outgrows brute force.

Every solved proof is written back to the library, so the system accrues skill over a run.

## The loop (`agent/orchestrator.py`)

```
target ─▶ spec-lint (reject trivial specs)
       ─▶ retrieve analogues ─▶ LLM: synthesize invariant set
       ─▶ render theorem (theorem_builder) ─▶ petanque.start
       ─▶ hammer ladder ──closed?──▶ Qed ─▶ premise/mutation gates ─▶ store in RAG
              └─residual goals─▶ DFS proof search (backtracking) ─▶ Qed | budget exhausted
```

Budgets bound invariant attempts and the search (depth, total tactic runs, LLM calls); escalation to
a stronger model triggers only after the local budget is spent. Non-vacuity is enforced in-proof
(`proof/premise_check.py` premise satisfiability; `eval/mutate.py` cycle-form mutation). The
orchestrator keeps an *optional* `fpga_oracle` veto hook, but that hardware track is **deferred /
parked** and is not wired on the critical path — output is proof-only.

## Proof search: DFS with backtracking (`agent/_discharge`)

Closing the residual goals is a **depth-first search with backtracking over petanque states**, not a
greedy repair loop. The earlier greedy version read only `goals[0]`, committed to the first tactic
that *applied* (which only means "no Rocq error", not "made progress"), discarded the prior state,
and abandoned the whole proof on any dead end — fatal for any proof that case-splits, because
`destruct_inv` always applies cleanly and there was no way back. The dominant failure mode on every
branching target (the FreeRTOS list set, ct-swap, chacha20).

- **A search node = a petanque state.** The state carries the full goal stack, so a `destruct_inv`
  fan-out (one subgoal per program point; branch points add taken/not-taken arms) is one node —
  success is simply `state.finished`. The search never hand-manages sibling subgoals.
- **Picinae tactics are opaque to the search.** It runs the candidate string and lets Rocq
  adjudicate; domain knowledge lives in the deterministic prelude, the hammer ladder, and RAG over
  vendored proofs — never in the search algorithm.
- **Frontier = explicit LIFO stack** (the DFS); `visited` (hash of the pretty-printed goal stack)
  prunes revisits. Hammer-first at every node; the structural prelude + reusable proof-skill library
  run at the root (advancing past `destruct_inv`), LLM tactic repair on the fan-out subgoals deeper,
  with a per-goal **past-failures** set so a known-bad tactic is never re-proposed. Children are
  pushed best-first; **backtracking is implicit** — a dead-end branch pushes nothing, so the stack
  pops back to the parent's next candidate, abandoning a bad split.
- **Safety rails:** a **per-tactic timeout** (`agent.tactic_timeout_s`) turns a hung
  `repeat step`/`psimpl` into a skipped rung instead of a stall, and **replay-from-root**
  (`PetanqueDriver.replay_from_root`) reconstructs a state from its tactic-path if a stored handle is
  ever rejected as stale (probing showed handles stay live, so this is a net, not the hot path).
- **Upgrade path:** swap the stack for a best-first priority queue once a cheap value signal exists
  (open-goal-count delta). DFS stays the default, fully tested path.

## What's genuinely ours vs. reused

Ours: the orchestration loop, invariant-synthesis and tactic-repair prompting, the RAG wiring,
the spec-lint/mutation/premise non-triviality gates, the C-intake compile/lift glue, the FastAPI
service + React GUI, the eval harness. (The FPGA measurement firmware/host also lives here but is
**parked** — see `docs/RESULTS.md`.) Everything load-bearing underneath — proof checking,
automation, the softcore timing model, the LSP — is established open source. That's the point.
