# cloq-agent

[![ci](https://github.com/RyanDavidNelson/cloq-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/RyanDavidNelson/cloq-agent/actions/workflows/ci.yml)

Agentic synthesis and machine-checking of **Cloq** timing proofs (WCET + constant-time)
over **Picinæ**-lifted RISC-V binaries, using a local LLM + retrieval, validated against a
real RISC-V softcore (**NEORV32**) on an **AMD AUP-ZU3** FPGA so that no proof is unsound or
trivially true.

See [`docs/SPEC.md`](docs/SPEC.md) for the full design, [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
for the tooling map, and [`docs/BRINGUP.md`](docs/BRINGUP.md) for board bring-up.

---

## What we reuse vs. what's ours

This repo deliberately reinvents **nothing** on the proof-engine side. The glue is the only new code.

| Concern | Off-the-shelf component (reused) | Our glue |
|---|---|---|
| Proof engine control | **petanque** (`pet-server`, ships in coq-lsp) + **pytanque** Python client | `proof/petanque_driver.py` |
| Document/corpus mining | **coqpyt** (sr-lab) over coq-lsp | `rag/index.py` (coqpyt optional, regex fallback) |
| Symbolic automation | **Cloq** `whammer`/`hammer`, **CoqHammer**, **Tactician** | `proof/hammer.py` (tactic ladder) |
| Timing framework | **Picinæ** + **Cloq** (`vendor/picinae`) | `proofs/` targets + `theorem_builder.py` |
| LLM serving | **vLLM** or **Ollama** (OpenAI-compatible HTTP) | `models.py` |
| Embeddings / vector search | **sentence-transformers** or any `/v1/embeddings` | `rag/embeddings.py`, `rag/store.py` |
| Agent tool protocol (optional) | **rocq-mcp** (LLM4Rocq); coq-lsp native MCP upcoming | — |
| Softcore | **NEORV32** (stnolting) packaged as Vivado IP | `fpga/` |
| Constant-time empirics | **dudect** methodology | `fpga/host/dudect.py` |

## Status (honest)

- **Working scaffolding** (real interfaces, runnable plumbing): containers, petanque driver,
  hammer ladder, RAG index/retrieve, orchestrator loop, eval harness, FPGA host/firmware/Vivado
  scripts, `addloop` smoke target.
- **The open research this repo exists to study** (not "solved" here): the *success rate* of
  LLM invariant synthesis on real Cloq targets. The plumbing that proposes an invariant, hands it
  to petanque, and measures whether `whammer` closes it is real; making that succeed often is the work.

---

## Quickstart

One command per profile (configs in `config/`, selected by `CLOQ_PROFILE` / `--profile`):

```bash
git clone --recurse-submodules <repo-url> cloq-agent && cd cloq-agent
cp .env.example .env          # set CLOQ_API_KEY (cloud model); the only manual step

# ---- profile: api  (web app, cloud model, no GPU/Ollama) ----
docker compose -f docker/compose.yaml --profile api up
#   -> GUI http://localhost:8080   API http://localhost:8000
```

```bash
# ---- profile: local  (CLI + local Ollama model + cloud escalation) ----
docker compose -f docker/compose.yaml --profile local up -d
docker compose -f docker/compose.yaml exec ollama ollama pull qwen3-coder:30b   # once
docker compose -f docker/compose.yaml run --rm agent prove addloop              # smoke (no LLM)
```

`rag_store/` and `runs/` are named volumes, so the corpus and job artifacts persist across
`up`/`down`. The first `up` builds the Rocq image (Picinæ/Cloq) and is slow; later runs are cached.
`config/default.yaml` is unchanged and still used when no profile is selected.

### Prove an uploaded C file (`prove-c`)

`prove-c` is the C-intake path: it compiles a self-contained C unit with the **pinned** NEORV32
toolchain (`docker/Dockerfile.toolchain`: `riscv64-unknown-elf-gcc` 14.2.0,
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
  memory-aliasing loop) is still **attempted** (free-form synthesis, needs a model). It is expected
  to fail; the report labels the ceiling class and says where the proof stalled, rather than
  pretending it is a crash:

  ```
  prove-c asum: NOT PROVED (expected failure for this ceiling class)
    class: array/pointer loop
    stages:
      [  ok  ] compile - -> asum.o
      [  ok  ] lift - entry=0x0 exits=['0x24']
      [xfail ] classify - array/pointer loop (expected failure: needs an exists-index loop invariant + witness); attempting anyway
      [xfail ] invariant - expected failure (array/pointer loop); stalled at: exhausted invariant attempts; attempts=12 iters=0 llm=12
    diagnosis: expected failure for array/pointer loop (needs an exists-index loop invariant + witness); proof stalled at: exhausted invariant attempts
  ```

Every run writes a structured report to `runs/prove_c/<func>/` as `report.json`, `report.md`, and
`report.html`, with per-stage status (`compile | lift | classify | spec-lint | invariant | repair |
stored`), the proven cycle-count closed form + predicted range on success, and the failing stage +
last residual goal + ceiling class on failure. A solved proof is written back into the RAG corpus
(`rag_store/`), surfaced as `stored: added to corpus`, so the next run can retrieve it.

The toolchain and `coqc` are pinned/matched to the timing model; run `prove-c` where the toolchain,
a model server, and the rocq image are reachable (the compose services), not against an arbitrary
host gcc.

FPGA oracle (on the AUP-ZU3, separately — see `fpga/README.md`):

```bash
# build the NEORV32-on-Zynq bitstream (host with Vivado)
vivado -mode batch -source fpga/vivado/build_neorv32_zynq.tcl

# measure on the board (PYNQ / Python on the A53)
python fpga/host/measure.py --target chacha20 --sweep-inputs 256
```

## HTTP API

The engine is exposed as a FastAPI service (`api/`, runnable with `uvicorn api.main:app` or
`docker compose up api` on port 8000). It takes **machine code** directly — a RISC-V ELF/object,
no source and no compile step — and runs `disassemble -> lift -> classify -> prove` in a worker
thread so the request returns immediately. The report a job produces is byte-for-byte the engine's
own (both go through `cloq_agent.pipeline.run_prove_machine_code`).

| route | purpose |
|---|---|
| `GET /health` | liveness + active model backend + config profile (never the API key) |
| `POST /jobs` | multipart machine-code upload + `mcu` (only `neorv32`) -> `{job_id}` (202) |
| `GET /jobs/{id}` | status + the structured report |
| `GET /jobs/{id}/stream` | Server-Sent Events: one message per stage transition, then a `final` event |
| `GET /corpus` | solved proofs stored in the RAG corpus |

```bash
curl -s localhost:8000/health
JID=$(curl -s -F mcu=neorv32 -F file=@program.o localhost:8000/jobs | jq -r .job_id)
curl -sN localhost:8000/jobs/$JID/stream        # watch disassemble -> lift -> classify -> ... live
curl -s localhost:8000/jobs/$JID | jq .report   # final structured report
```

(The CLI `prove-c` still takes C source and compiles it; the web app takes machine code directly.)

## Web GUI

A single-page app (`gui/`, Vite + React) wraps the API behind the **AutoCloq** wordmark and the
tagline *AI-Generated, Formally-Verified, Tight Timing Constraints for Machine Code*. Pick the
microcontroller (only **NEORV32** today; others "coming soon"), drop in a RISC-V machine-code file,
and watch the pipeline stream `disassemble -> lift -> prove -> result` live. The result view renders
the proof — closed form + predicted range + a link to the stored corpus entry — or, for a real
data-structure loop, the **diagnostic as a primary view** (failing stage, last residual goal,
ceiling-class label), not a toast.

```bash
docker compose -f docker/compose.yaml --profile api up   # rocq + api + gui
# open http://localhost:8080
```

nginx in the GUI image reverse-proxies `/api` to the api service (same origin, SSE passes through).

## Layout

```
proofs/        Rocq targets + build (addloop smoke target; ct-swap/chacha20 to add)
src/cloq_agent/
  proof/       petanque driver, hammer ladder, theorem assembly
  rag/         embeddings, vector store, corpus indexer, retriever
  agent/       invariant synthesis, tactic repair, the orchestration loop
  lift/        CFG + loop detection over lifted IL / objdump
  cli.py       index | prove | eval entrypoints
fpga/          Vivado BD tcl, NEORV32 measurement firmware, PYNQ host driver, dudect
eval/          target list w/ gold cycle counts, harness, mutation test, ablations
docker/        Rocq+coq-lsp image, agent image, compose
docs/          SPEC, ARCHITECTURE, BRINGUP
```

## Continuous integration

GitHub Actions (`.github/workflows/`):

- **`ci.yml`** (push / PR) — `lint` (`ruff check src eval`), `pytest` (the hosted runner has no
  pet-server, so the integration-search tests skip; everything else passes), and `rocq-smoke`,
  which builds the rocq image and proves `addloop` via `make -C proofs smoke`. `rocq-smoke` is a
  real gate, not `allow_failure`.
- **`build.yml`** (push to `main`, `v*` tags, manual) — builds the container images
  (`Dockerfile.rocq`, `Dockerfile.agent`, and `Dockerfile.toolchain` / `Dockerfile.gui` once they
  exist) and pushes them to GHCR on version tags. No FPGA images.
- **`nightly.yml`** (scheduled, **self-hosted** runner with GPU + Ollama + pet-server) — re-runs
  `eval list_easy_four` and `eval loop_easy` and fails if any pinned-passing target regresses. The
  expected pass set (`list_easy_four` 3/4, `loop_easy` 1/3) is pinned in `eval/regression_gate.py`.

The old `.gitlab-ci.yml` was **removed**: GitHub is now the project's CI home and maintaining two
pipelines would let them drift. The three workflows above are a faithful port of its
`lint -> build -> test -> eval` stages.

## License

Glue code: MIT (see `LICENSE`). `vendor/picinae` retains its own license — check it before redistribution.
NEORV32, coq-lsp, pytanque, coqpyt, CoqHammer, Tactician each carry their own (permissive) licenses.
