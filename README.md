# cloq-agent

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

```bash
# 0. clone with the Picinæ/Cloq submodule
git submodule update --init --recursive   # populates vendor/picinae

# 1. bring up Rocq + coq-lsp + petanque + Picinæ/Cloq, and the agent container
docker compose -f docker/compose.yaml up -d

# 2. point the agent at a local model (Ollama on the host, or vLLM)
ollama pull qwen3-coder:30b           # workhorse; fits a 32GB 5090

# 3. index the Picinæ/Cloq corpus + any solved proofs into the RAG store
uv run cloq-agent index

# 4. prove the smoke target end-to-end (hammer-only, no LLM needed)
uv run cloq-agent prove addloop

# 5. run the full eval over the starter targets
uv run cloq-agent eval
```

FPGA oracle (on the AUP-ZU3, separately — see `fpga/README.md`):

```bash
# build the NEORV32-on-Zynq bitstream (host with Vivado)
vivado -mode batch -source fpga/vivado/build_neorv32_zynq.tcl

# measure on the board (PYNQ / Python on the A53)
python fpga/host/measure.py --target chacha20 --sweep-inputs 256
```

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

## License

Glue code: MIT (see `LICENSE`). `vendor/picinae` retains its own license — check it before redistribution.
NEORV32, coq-lsp, pytanque, coqpyt, CoqHammer, Tactician each carry their own (permissive) licenses.
