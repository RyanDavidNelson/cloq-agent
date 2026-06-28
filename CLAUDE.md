# CLAUDE.md — cloq-agent

> Guidance for Claude Code working in this repo. Read this first, then `TASKS.md`.

## Project

`cloq-agent`: agentic synthesis + machine-checking of **Cloq** timing proofs (WCET +
constant-time) over **Picinæ**-lifted RISC-V binaries, validated against the **NEORV32**
softcore (optionally on an **AMD AUP-ZU3** FPGA). The LLM only *proposes*; Rocq *checks*.
Nothing the model writes enters the trusted artifact without passing the prover.

We are turning this from a CLI research prototype into a **cloneable, compose-up application**
a research lab can run end-to-end: upload a C file → pick MCU/arch/compiler (only RISC-V /
NEORV32 today) → compile → lift → prove → get **either** a proof with a cycle-count closed
form + predicted range **or** a structured failure diagnostic. Successful proofs/invariants
are written back into the RAG corpus (already implemented in the orchestrator).

## Golden rules

1. **Do not break current functionality.** `docker compose ... run --rm agent prove addloop`
   and the `index | prove | eval` CLI must keep working at every step.
2. **Generator–verifier discipline stays.** Never let the GUI or new code accept an LLM
   artifact as trusted. Soundness comes from Rocq, not from any parser, the GUI, or the LLM.
3. **FPGA is optional.** The formal proof (closed form + predicted range) must work with no
   board present. Measured-vs-predicted only appears when an FPGA oracle is configured.
4. **Pin everything that affects cycle counts.** Compiler, flags, NEORV32 commit, SoC config.
   Changing `-O` level changes instruction selection → changes the timing model. The timing
   model and the compile flags are a matched pair; document any change.
5. **Secrets via env only.** API keys come from env / `.env` (compose `env_file`). Never
   commit a key, never bake one into an image, never log it.

## Repo layout (current + planned)

```
src/cloq_agent/
  proof/      petanque driver, hammer ladder, theorem_builder        [exists]
  rag/        embeddings, store, index, retriever                     [exists]
  agent/      orchestrator, invariant_synth, tactic_repair            [exists]
  lift/       cfg.py (objdump→CFG)                                    [exists]
  lift/       compile.py (C→ELF/obj via riscv32-gcc) + riscv_lifter   [ADD]
  report.py   ProofResult → structured diagnostic (json + html/md)    [ADD]
  cli.py      index | prove | eval (+ `compile`, `prove-c`)           [extend]
api/          FastAPI service wrapping the orchestrator (SSE/WS)       [ADD]
gui/          frontend SPA (upload C, pick target, stream, render)    [ADD]
config/       default.yaml (+ local.yaml, api.yaml profiles)          [extend]
proofs/       Rocq targets + build (addloop smoke)                    [exists]
eval/         targets.yaml, harness, mutate, ablations                [exists]
eval/transfer/  OpenSSL + FreeRTOS transfer suite (20 targets)        [ADD]
fpga/         NEORV32 oracle: vivado/firmware/host                    [exists]
docker/       Dockerfile.rocq, Dockerfile.agent, compose.yaml         [extend]
docker/       Dockerfile.toolchain, Dockerfile.gui                    [ADD]
.github/workflows/  ci.yml, build.yml, nightly.yml                    [ADD]
docs/         SPEC, ARCHITECTURE, BRINGUP, FILEMAP                     [exists]
```

## How to run (current)

- Proof engine: `docker compose -f docker/compose.yaml up -d --build rocq`
- Smoke build: `docker compose -f docker/compose.yaml exec rocq bash -lc 'eval $(opam env); cd /work/proofs && coq_makefile -f _CoqProject -o Makefile.coq && make -f Makefile.coq'`
- Agent end-to-end: `docker compose -f docker/compose.yaml run --rm agent prove addloop`

## How it should run (target state)

- `cp .env.example .env` and fill `CLOQ_API_KEY` if using the API profile.
- Local + escalate:  `docker compose --profile local up`  (Ollama bundled or on host)
- API-key only:      `docker compose --profile api up`    (no GPU/Ollama needed)
- Open the GUI at `http://localhost:8080`, upload a `.c`, pick NEORV32, run.
- `rag_store/` and `runs/` are named volumes so the corpus + outputs persist.

## Conventions

- Python: ruff-clean (`ruff check src eval api fpga/host`), typed dataclasses, no new heavy
  deps without reason. Keep the "reuse the engine, write only glue" rule.
- New config keys: add to `config/default.yaml`, the typed dataclasses in `config.py`, and
  make them overridable via `CLOQ_<SECTION>_<KEY>` env vars (the existing pattern).
- Every new pipeline stage emits a structured record into the `ProofResult`/report so the GUI
  can show *which stage failed and why* (compile / lift / spec-lint / no-invariant /
  repair-budget / fpga-disagree).
- Tests: add a pytest per new module; CI must stay green. The `rocq-smoke` job loses
  `allow_failure` the moment Phase 0 closes addloop.

## A target is "properly configured" when it has

A compilable self-contained C unit + pinned flags, a lifted `.v` (program map + start/end
addrs), a `targets.yaml` entry (`entry_addr`, `exit_point`, `theorem_name`, `params`,
`description`, `objdump`, and for CT a `secret_param`), a stated property (WCET or
constant-time), and — where one exists — a gold cycle count. New transfer targets without a
paper/FPGA gold are still valid: the metric is "sound + non-vacuous proof produced," recorded
under `runs/transfer/<suite>/<target>/`.

See `TASKS.md` for the ordered, acceptance-criteria'd plan.
