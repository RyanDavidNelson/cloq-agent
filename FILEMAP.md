# File map

A guide to every file in `cloq-agent`, grouped by area, with a one-line explanation of each.
The design rule throughout: **reuse the proof-engine stack wholesale, write only the glue.**

```
cloq-agent/
├── README.md  pyproject.toml  LICENSE  NOTICE.md  .gitmodules  .gitignore
├── .github/workflows/  ci.yml  build.yml  nightly.yml
├── config/
├── src/cloq_agent/{proof,rag,agent,lift}/
├── proofs/{targets,lib}/
├── eval/{targets}/
├── fpga/{vivado,firmware,host}/
├── docker/
├── docs/
└── tests/
```

## Root — project metadata & entry points

| File | What it is |
|---|---|
| `README.md` | Overview, the reuse-vs-ours table, quickstart, honest status of what's real vs. open research. |
| `pyproject.toml` | Package metadata, dependencies, `uv` git-sources for pytanque/coqpyt, and the `cloq-agent` CLI script. |
| `LICENSE` | MIT — applies to this repo's glue code only. |
| `NOTICE.md` | Third-party components and their licenses (Picinæ, coq-lsp, NEORV32, CoqHammer, …). |
| `.gitmodules` | Pins `vendor/picinae` as a git submodule. |
| `.gitignore` | Ignores build artifacts: `.vo`, bitstreams, `rag_store/`, `runs/`. |
| `.github/workflows/` | CI: `ci.yml` (ruff → pytest → Rocq smoke), `build.yml` (images → GHCR on tags), `nightly.yml` (eval regression gate). |

## config/ — all runtime knobs in one place

| File | What it is |
|---|---|
| `default.yaml` | Petanque host/port, model endpoint + name, RAG settings, agent budgets, FPGA tolerances. Every key is overridable via `CLOQ_*` env vars. |
| `local.yaml` | Profile (`--profile local` / `CLOQ_PROFILE=local`): Ollama primary + cloud escalation. Overlaid on `default.yaml`. |
| `api.yaml` | Profile (`--profile api`): cloud-API primary, escalation off, no Ollama/GPU. Overlaid on `default.yaml`. |

## src/cloq_agent/ — the agent library

| File | What it is |
|---|---|
| `__init__.py` | Version marker. |
| `config.py` | Loads `default.yaml` / a profile (`local`/`api`, overlaid on default) into typed dataclasses; applies `CLOQ_*` env overrides incl. `CLOQ_API_KEY`. |
| `.env.example` | Template for `.env` (git-ignored): `CLOQ_API_KEY`, `CLOQ_MODEL_NAME`, `CLOQ_MODEL_BASE_URL`. |
| `models.py` | LLM client over any OpenAI-compatible endpoint (vLLM/Ollama), with optional escalation to a stronger model for hard goals. |
| `cli.py` | The `index | prove | prove-c | eval | doctor` commands. |
| `report.py` | Structured prove-c diagnostic: per-stage records (compile/lift/classify/spec-lint/invariant/repair/stored), ceiling labelling, NEORV32 predicted range, rendered to text / JSON / Markdown / HTML. |
| `pipeline.py` | The prove pipeline, shared so the CLI and API reports never drift: `run_prove_c` (compile C, CLI) and `run_prove_machine_code` (disassemble an ELF/object, API/GUI) over one `lift -> classify -> prove` body; `on_stage` hook for live streaming. |

### proof/ — talking to the Rocq engine (reused tooling underneath)

| File | What it is |
|---|---|
| `petanque_driver.py` | Thin typed wrapper over **pytanque**: start a proof, run a tactic, read goals. The only code that touches the engine. |
| `hammer.py` | The automation ladder (Cloq `whammer` → CoqHammer → `lia`) tried before any LLM call. |
| `theorem_builder.py` | Renders a complete Cloq timing-theorem `.v` file (boilerplate + the agent's invariant) for petanque to load. |

### rag/ — retrieval (the highest-leverage glue)

| File | What it is |
|---|---|
| `store.py` | Transparent numpy/JSONL cosine vector store (swap in FAISS/Chroma later behind the same interface). |
| `embeddings.py` | Embeddings with three backends: local sentence-transformers / a `/v1` endpoint / a hash fallback so CI runs offline. |
| `index.py` | Mines Picinæ/Cloq lemmas + definitions and solved proofs into the store (coqpyt if present, regex fallback otherwise). |
| `retriever.py` | Queries by goal-state or CFG description; returns lemmas and prior proofs separately, since they fill different prompt roles. |

### agent/ — the loop and the creative steps

| File | What it is |
|---|---|
| `invariant_synth.py` | Prompts the model to produce a `timing_invs` set from the CFG + retrieved analogues. The one genuinely creative step. |
| `tactic_repair.py` | Prompts the model for candidate next tactics on a stuck goal. |
| `orchestrator.py` | The full loop: spec-lint → synthesize → render → hammer → retrieve + repair → FPGA veto → store. Budgeted throughout. |

### lift/ — code-shape recovery for prompting

| File | What it is |
|---|---|
| `cfg.py` | Parses a RISC-V objdump into basic blocks and finds back-edges (loop headers); derives loop/straight-line timing terms. A prompt aid, not a trusted component. |
| `compile.py` | Intake front doors: `compile_c` (pinned `riscv64-unknown-elf-gcc` -> RV32 object, CLI) and `load_machine_code` (objdump an uploaded ELF/object, GUI), both yielding the `objdump` listing the lifter consumes. |
| `intake.py` | C-intake `lift` stage: runs `riscv_lifter.sh`, generates the Cloq `TimingProof` scaffolding, classifies against the proof-engine ceiling, builds the `TargetSpec` (CFG-derived invariant + discharge for the straight-line case). |

## api/ — the HTTP service (FastAPI)

| File | What it is |
|---|---|
| `main.py` | `create_app` + routes: `GET /health`, `POST /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/stream` (SSE), `GET /corpus`. |
| `service.py` | `JobManager`: runs each upload's prove-c pipeline in a worker thread; collects stage events; lists the stored corpus. |

## gui/ — the web SPA (Vite + React)

| File | What it is |
|---|---|
| `src/App.tsx` | Orchestrates upload -> SSE stream -> result; holds health + corpus state. |
| `src/components/` | `Header` (AutoCloq ASCII hero + tagline), `UploadForm` (MCU dropdown + machine-code upload), `StageProgress` (live stepper + log), `ResultPanel` (proof or first-class diagnostic), `CorpusPanel`. |
| `src/ascii.ts` | The AutoCloq wordmark (figlet ansi_shadow), rendered white-on-black in the hero. |
| `src/api.ts` | Same-origin `/api` client incl. the SSE subscription. |

## proofs/ — the Rocq side

| File | What it is |
|---|---|
| `_CoqProject` | Maps the vendored Picinæ/Cloq paths for the build. |
| `Makefile` | `coq_makefile`-based build; `make smoke` builds just `addloop`. |
| `targets/Addloop.v` | Paper-faithful timing proof (Fig 2–4). Doubles as the smoke test and the gold invariant source. **Reconcile its identifiers with your vendored Cloq before first build.** |
| `lib/` | Empty — for shared Rocq helper lemmas as the corpus grows. |

## eval/ — measuring whether it works

| File | What it is |
|---|---|
| `targets.yaml` | The target list with gold cycle counts (chacha20 = 13624, vlist_insert_end = 54, …) and secret-param tags. |
| `targets.py` | Loads a target → `TargetSpec` + CFG description + gold invariant. |
| `harness.py` | Runs the orchestrator over all targets and tabulates the metrics. |
| `mutate.py` | Mutation/metamorphic testing: inject a leak, require the proof to break **and** the FPGA to show variance (anti-vacuity). |
| `ablations.py` | Toggles RAG / hammer-first / escalation and re-runs, to reproduce the retrieval finding on your own targets. |
| `targets/addloop.objdump` | Sample disassembly so the CFG/loop-detection path is exercised end-to-end. |

## fpga/ — the hardware oracle (AMD AUP-ZU3)

| File | What it is |
|---|---|
| `README.md` | Oracle design, the PS↔PL split, determinism rules, the three anti-vacuity checks. |
| `vivado/build_neorv32_zynq.tcl` | Builds the NEORV32-in-PL + Zynq-PS block design → bitstream + `.xsa`. **Verify the part/speed-grade string.** |
| `firmware/mailbox.h` | AXI register map shared between the A53 host and the NEORV32 core. |
| `firmware/measure_stub.c` | NEORV32 firmware: per GO pulse, run the target bracketed by `mcycle`/`minstret` reads. |
| `firmware/Makefile` | Builds the firmware against the NEORV32 software framework. |
| `host/measure.py` | Runs on the PS under PYNQ: sweep inputs, read cycles, compare measured vs Cloq-predicted; check secret-invariance. |
| `host/dudect.py` | Fixed-vs-random Welch t-test on `mcycle` distributions — a sharp constant-time leak detector. |

## docker/ — reproducible environments

| File | What it is |
|---|---|
| `Dockerfile.rocq` | Rocq 8.20 + coq-lsp + petanque + CoqHammer + Tactician, Picinæ/Cloq prebuilt; launches `pet-server`. |
| `Dockerfile.agent` | The Python agent image. |
| `Dockerfile.toolchain` | Pinned RISC-V cross toolchain (gcc 14.2 / binutils 2.44) for the C-intake `compile` stage; records its exact version. |
| `Dockerfile.api` | Backend API image (toolchain base + Python + `[api,rag]`); serves `uvicorn api.main:app` on :8000. |
| `Dockerfile.gui` | Builds the SPA (node) and serves it with nginx on :8080; `nginx.conf` reverse-proxies `/api` -> api. |
| `nginx.conf` | GUI server: static SPA + SSE-friendly `/api` reverse proxy to the api service. |
| `compose.yaml` | Wires the rocq (petanque) service + agent; the model server stays on the host so the GPU does too. |

## docs/

| File | What it is |
|---|---|
| `SPEC.md` | Full project spec: milestones M0–M5, eval metrics, FPGA oracle design, job-requirement mapping. |
| `ARCHITECTURE.md` | The reuse map and the loop, in prose — why each off-the-shelf piece was chosen. |
| `BRINGUP.md` | The two-track (software ‖ FPGA) bring-up checklist and target attack order. |
| `FILEMAP.md` | This file. |

## tests/

| File | What it is |
|---|---|
| `test_cfg.py` | CFG recovery + loop detection on the addloop listing. |
| `test_store.py` | Vector-store add/query/persistence round-trip. |
| `test_theorem_builder.py` | Theorem rendering produces a well-formed, parametrized `.v`. |

---

### Key references

- **Rango** — Thompson, Saavedra, Carrott, Fisher, Sanchez-Stern, Brun, Ferreira, Lerner, First.
  *Adaptive Retrieval-Augmented Proving for Automated Software Verification.* ICSE 2025
  (Distinguished Paper). arXiv:2412.14063. — the RAG approach: retrieve premises **and** prior proofs.
- **CoqPyt** — Carrott et al. *Proof Navigation in Python in the Era of LLMs.* arXiv:2405.04282. — corpus mining client.
- **coq-lsp / Flèche / petanque** — Gallego Arias et al. — the proof-engine API layer.
- **CoqHammer** — Czajka & Kaliszyk, JAR 2018. **Tactician** — Blaauwbroek et al. — automation.
- **Cloq** — Averill. *Formally-Verified, Tight Timing Constraints for Machine Code.* PLDI SRC 2025. — the timing framework.
- **Picinæ** — the binary-analysis framework Cloq builds on.
- **NEORV32** — Nolting — the RISC-V softcore used as the hardware oracle.
