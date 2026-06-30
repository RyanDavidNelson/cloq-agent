```
 █████╗ ██╗   ██╗████████╗ ██████╗  ██████╗██╗      ██████╗  ██████╗
██╔══██╗██║   ██║╚══██╔══╝██╔═══██╗██╔════╝██║     ██╔═══██╗██╔═══██╗
███████║██║   ██║   ██║   ██║   ██║██║     ██║     ██║   ██║██║   ██║
██╔══██║██║   ██║   ██║   ██║   ██║██║     ██║     ██║   ██║██║▄▄ ██║
██║  ██║╚██████╔╝   ██║   ╚██████╔╝╚██████╗███████╗╚██████╔╝╚██████╔╝
╚═╝  ╚═╝ ╚═════╝    ╚═╝    ╚═════╝  ╚═════╝╚══════╝ ╚═════╝  ╚══▀▀═╝
```

### AI-Generated, Formally-Verified, Tight Timing Constraints for Machine Code

[![ci](https://github.com/RyanDavidNelson/cloq-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/RyanDavidNelson/cloq-agent/actions/workflows/ci.yml)

**cloq-agent / AutoCloq** is agentic synthesis and machine-checking of **Cloq** timing proofs
(WCET + constant-time) over **Picinæ**-lifted RISC-V binaries, using an LLM + retrieval. The
timing model is calibrated to the **NEORV32** softcore. The LLM only *proposes*; **Rocq checks** —
nothing the model writes enters the trusted artifact without passing the prover. The output of a
run is **either** a machine-checked proof with a cycle-count closed form + predicted range **or**
a structured failure diagnostic that names *which stage failed and why*.

> **Trust basis (proof-only).** Output is the proven **closed-form cycle count + predicted range**.
> Proofs are sound *relative to* the NEORV32 timing model (a trusted, hardware-unvalidated input)
> and the pinned compiler/flags. Constant-time is **formal-only** (the secret provably never enters
> the closed form, `spec_lint`-enforced). The FPGA hardware oracle is **deferred / parked**
> (see [Deferred: FPGA](#deferred-fpga-hardware-oracle)) — there is no board dependency anywhere on
> the build, run, or CI path.

See [`docs/SPEC.md`](docs/SPEC.md) for the design, [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
for the tooling map, [`docs/RESULTS.md`](docs/RESULTS.md) for what works and the ceiling, and
[`FILEMAP.md`](FILEMAP.md) for a file-by-file guide.

---

## What we reuse vs. what's ours

This repo deliberately reinvents **nothing** on the proof-engine side. The glue is the only new code.

| Concern | Off-the-shelf component (reused) | Our glue |
|---|---|---|
| Proof engine control | **petanque** (`pet-server`, ships in coq-lsp) + **pytanque** Python client | `proof/petanque_driver.py` |
| Document/corpus mining | **coqpyt** (sr-lab) over coq-lsp | `rag/index.py` (coqpyt optional, regex fallback) |
| Symbolic automation | **Cloq** `whammer`/`hammer`, **CoqHammer**, **Tactician** | `proof/hammer.py` (tactic ladder) |
| Timing framework | **Picinæ** + **Cloq** (`vendor/picinae`) | `proofs/` targets + `theorem_builder.py` |
| C → RV32 toolchain | **riscv64-unknown-elf-gcc** (pinned) | `lift/compile.py` |
| LLM serving | **vLLM** or **Ollama** (OpenAI-compatible HTTP), or any cloud `/v1` | `models.py` |
| Embeddings / vector search | **sentence-transformers** or any `/v1/embeddings` | `rag/embeddings.py`, `rag/store.py` |
| Softcore (timing-model reference) | **NEORV32** (stnolting) | `lift/cfg.py` timing table |
| Constant-time empirics (deferred) | **dudect** methodology | `fpga/` — *parked* |

## Status (honest)

The proof engine is **well past bring-up**. The smoke target proves end-to-end and the methodology
is in place. See [`docs/RESULTS.md`](docs/RESULTS.md) for the evidence and the precise ceiling.

- **Straight-line** WCET/constant-time and the **pure counter loop** synthesize-and-prove
  end-to-end. **Array/pointer loops** (ct_swap) discharge to Qed *given* the right invariant.
- **Held-out generalization** ([`docs/results/transfer.md`](docs/results/transfer.md)): **10/10**
  branchless straight-line targets reduced from pinned OpenSSL 3.4.0 + FreeRTOS V11.1.0 prove to
  Qed with a CFG-derived deterministic proof (gold withheld from the library + few-shot, so a pass
  is generalization, not recall). Medium/hard hit the documented ceiling classes by design.
- **The ceiling** (the research frontier): data-structure loops needing a decidability case-split
  (search early-exit) or `noverlaps` memory-aliasing reasoning. The pipeline labels these as
  expected-limitation diagnostics rather than crashing.

---

## Quickstart

Two profiles, selected by `--profile` (configs in `config/`). The **api** profile is the one-command
web app against a cloud model — nothing to install but Docker. The **local** profile adds an Ollama
model server for a fully local run.

```bash
git clone --recurse-submodules <repo-url> cloq-agent && cd cloq-agent
cp .env.example .env          # set CLOQ_API_KEY for the cloud model (only manual step for `api`)

# ---- profile: api  (web app, cloud model, no GPU/Ollama) ----
docker compose -f docker/compose.yaml --profile api up
#   -> GUI http://localhost:8080   API http://localhost:8000
```

```bash
# ---- profile: local  (CLI + local Ollama model + optional cloud escalation) ----
docker compose -f docker/compose.yaml --profile local up -d
docker compose -f docker/compose.yaml exec ollama ollama pull qwen3-coder:30b   # once
docker compose -f docker/compose.yaml run --rm agent prove addloop              # smoke (no LLM)
```

`rag_store/` and `runs/` are named volumes, so the corpus and job artifacts persist across
`up`/`down`. The first `up` builds the Rocq image (Picinæ/Cloq) and is slow; later runs are cached.
`config/default.yaml` is used when no profile is selected.

> **You need an LLM only for synthesis.** Straight-line and counter-loop targets prove with **no
> model** (the invariant + discharge are derived from the CFG). Free-form synthesis on harder
> targets needs either a cloud key (`CLOQ_API_KEY`, `api` profile) or a local Ollama (`local`).

## Web GUI

A single-page app (`gui/`, Vite + React) wraps the API behind the **AutoCloq** wordmark and the
tagline *AI-Generated, Formally-Verified, Tight Timing Constraints for Machine Code*. You:

1. pick the microcontroller (only **NEORV32** today; others listed as "coming soon"),
2. drop in **either a C source file (`.c`) or a prebuilt RISC-V ELF/object** —
   - a **C file is compiled in-process** with the pinned RISC-V GCC
     (`-march=rv32im_zicsr_zicntr -mabi=ilp32 -O2`, the same front door as the CLI `prove-c`); you
     can name the function to prove (defaults to the file stem),
   - a **binary** is disassembled directly (no compile step),
3. watch the pipeline stream `compile`/`disassemble → lift → classify → prove → result` live, and
4. read the result: the proof (closed form + predicted range + a link to the stored corpus entry),
   or — for a data-structure loop — the **diagnostic as a primary view** (failing stage, last
   residual goal, ceiling-class label), not a toast.

```bash
docker compose -f docker/compose.yaml --profile api up   # rocq + api + gui
# open http://localhost:8080
```

nginx in the GUI image reverse-proxies `/api` to the api service (same origin, SSE passes through).

## HTTP API

The engine is exposed as a FastAPI service (`api/`, runnable with `uvicorn api.main:app` or
`docker compose --profile api up` on port 8000). `POST /jobs` takes a multipart upload that is
**either C source or a RISC-V ELF/object**: a `.c`/`.i` upload is compiled with the pinned RISC-V
GCC first; anything else is disassembled directly. Both converge on the same
`lift → classify → prove` body in a worker thread, so the report is byte-for-byte the engine's own
(the API and CLI both call into `cloq_agent.pipeline`).

| route | purpose |
|---|---|
| `GET /health` | liveness + active model backend + config profile (never the API key) |
| `POST /jobs` | multipart upload (C source or ELF/object) + `mcu` (only `neorv32`), optional `func`/`property`/`secret` → `{job_id}` (202) |
| `GET /jobs/{id}` | status + the structured report |
| `GET /jobs/{id}/stream` | Server-Sent Events: one message per stage transition, then a `final` event |
| `GET /corpus` | solved proofs stored in the RAG corpus |

```bash
curl -s localhost:8000/health
# C source: compiled with the pinned toolchain, then proven
JID=$(curl -s -F mcu=neorv32 -F func=sum3 -F file=@sum3.c localhost:8000/jobs | jq -r .job_id)
# or a prebuilt object: disassembled directly
JID=$(curl -s -F mcu=neorv32 -F file=@program.o localhost:8000/jobs | jq -r .job_id)
curl -sN localhost:8000/jobs/$JID/stream        # watch the stages live
curl -s localhost:8000/jobs/$JID | jq .report   # final structured report
```

## CLI: prove an uploaded C file (`prove-c`)

`prove-c` is the C-intake path from the terminal: it compiles a self-contained C unit with the
**pinned** NEORV32 toolchain (`docker/Dockerfile.toolchain`: `riscv64-unknown-elf-gcc` 14.2.0,
`-march=rv32im_zicsr_zicntr -mabi=ilp32 -O2 -ffreestanding -nostdlib`), lifts it with the vendored
`riscv_lifter.sh`, builds the Cloq `TimingProof` scaffolding + a CFG-derived theorem, then either
proves it or returns a structured diagnostic.

```bash
cloq-agent prove-c sum3.c --func sum3                 # WCET (default)
cloq-agent prove-c ct_pick.c --func ct_pick --property ct --secret key
```

- A **straight-line** function proves end-to-end with **no model**: the invariant and the discharge
  are derived from the CFG (the pinned cycle closed form is the sum of per-instruction timings).
- A function that hits the proof-engine ceiling (array/pointer, search early-exit, or
  memory-aliasing loop) is still **attempted** under `--force-synthesis` (free-form synthesis, needs
  a model). It is expected to fail; the report labels the ceiling class and says where the proof
  stalled, rather than pretending it is a crash:

  ```
  prove-c asum: NOT PROVED (expected failure for this ceiling class)
    class: array/pointer loop
    stages:
      [  ok  ] compile - -> asum.o
      [  ok  ] lift - entry=0x0 exits=['0x24']
      [xfail ] classify - array/pointer loop (expected failure: needs an exists-index loop invariant + witness)
      [xfail ] invariant - expected failure (array/pointer loop); stalled at: exhausted invariant attempts
    diagnosis: expected failure for array/pointer loop (needs an exists-index loop invariant + witness)
  ```

Every run writes a structured report to `runs/prove_c/<func>/` as `report.json`, `report.md`, and
`report.html`, with per-stage status (`compile | lift | classify | spec-lint | invariant | repair |
stored`), the proven cycle-count closed form + predicted range on success, and the failing stage +
last residual goal + ceiling class on failure. A solved proof is written back into the RAG corpus
(`rag_store/`), surfaced as `stored: added to corpus`, so the next run can retrieve it.

The toolchain and `coqc` are pinned/matched to the timing model; run `prove-c` where the toolchain,
a model server, and the rocq image are reachable (the compose services), not against an arbitrary
host gcc.

## Layout

```
proofs/        Rocq targets + build (addloop smoke target)
src/cloq_agent/
  proof/       petanque driver, hammer ladder, theorem assembly
  rag/         embeddings, vector store, corpus indexer, retriever
  agent/       invariant synthesis, tactic repair, the orchestration loop
  lift/        compile (C→RV32), CFG + loop detection, intake/lift, search templates
  cli.py       index | prove | prove-c | eval | replay | doctor
api/           FastAPI service: upload C or a binary, run jobs in a worker, stream stages (SSE)
gui/           Vite + React SPA (AutoCloq): upload, live stages, proof / diagnostic
config/        default.yaml + local.yaml / api.yaml profiles
eval/          target list, harness, mutation test, ablations, held-out transfer suite
docker/        Rocq+coq-lsp image, agent, toolchain, api, gui, compose
docs/          SPEC, ARCHITECTURE, RESULTS, BRINGUP, results/transfer
fpga/          NEORV32 hardware oracle — DEFERRED, off the critical path (see below)
```

## Continuous integration

The project ships **two equivalent pipelines** so it runs the same on GitHub or GitLab. Keep them
in sync if you change one.

**GitHub Actions** (`.github/workflows/`):

- **`ci.yml`** (push / PR) — `lint` (`ruff check src eval api`), `pytest` (the hosted runner has no
  pet-server, so the integration-search tests skip; everything else passes), and `rocq-smoke`,
  which builds the rocq image and proves `addloop` via `make -C proofs smoke`. `rocq-smoke` is a
  real gate, not `allow_failure`.
- **`build.yml`** (push to `main`, `v*` tags, manual) — builds the container images
  (`Dockerfile.rocq`, `.agent`, `.toolchain`, `.api`, `.gui`) and pushes them to GHCR on version
  tags. No FPGA images.
- **`nightly.yml`** (scheduled, **self-hosted** runner with GPU + Ollama + pet-server) — re-runs
  `eval list_easy_four` and `eval loop_easy` and fails if any pinned-passing target regresses
  (`eval/regression_gate.py`).

**GitLab CI** (`.gitlab-ci.yml`) — the same `lint → test → smoke → build` stages, ported for GitLab
runners (Docker-in-Docker for the image build/smoke). Use this when mirroring to GitLab.

## License

Glue code: MIT (see `LICENSE`). `vendor/picinae` retains its own license — check it before
redistribution. NEORV32, coq-lsp, pytanque, coqpyt, CoqHammer, Tactician each carry their own
(permissive) licenses; see [`NOTICE.md`](NOTICE.md).

## Deferred: FPGA hardware oracle

The original design (`docs/SPEC.md`) included an **FPGA validation oracle** — NEORV32 on an
AMD AUP-ZU3, measuring `mcycle` and checking measured-vs-predicted and dudect-style constant-time.
That track is **parked at the user's request**. `fpga/` stays in the repo for provenance but is
**off the critical path**: no board dependency in the build, GUI, API, report, CI, or transfer
metric. The current output is **proof-only** (closed form + predicted range). Two in-proof
anti-vacuity gates stand in for the checks hardware used to provide:

- `eval/mutate.py` (proof-only) — corrupt the cycle closed form and the proof **must** break
  (postcondition non-vacuity);
- `proof/premise_check.py` — the **premise-satisfiability gate**, run on every `prove()`: a
  contradictory premise is rejected at generation time instead of yielding a vacuously-true theorem.
