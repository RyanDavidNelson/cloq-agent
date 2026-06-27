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
       ─▶ hammer ladder ──closed?──▶ Qed ─▶ FPGA oracle ─▶ store in RAG
              └─residual goals─▶ retrieve + LLM tactic repair ─▶ (loop, budgeted)
```

Budgets bound invariant attempts and repair iterations; escalation to a stronger model triggers
only after the local budget is spent. The FPGA oracle can veto a "proved" result if measured
cycles disagree with the prediction (an unsound timing model).

## What's genuinely ours vs. reused

Ours: the orchestration loop, invariant-synthesis and tactic-repair prompting, the RAG wiring,
the spec-lint/mutation non-triviality gates, the FPGA measurement firmware/host, the eval harness.
Everything load-bearing underneath — proof checking, automation, the softcore, the LSP — is
established open source. That's the point.
