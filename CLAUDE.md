## Project
cloq-agent: agentic synthesis + machine-checking of Cloq timing proofs over
Picinae-lifted RISC-V binaries. Two containers via docker/compose.yaml:
`rocq` (pet-server on :8765) and `agent` (the Python orchestrator).

## How to run
- Build/up proof engine: `docker compose -f docker/compose.yaml up -d --build rocq`
- Smoke proof (hand-built): `docker compose -f docker/compose.yaml exec rocq bash -lc 'eval $(opam env); cd /work/proofs && coq_makefile -f _CoqProject -o Makefile.coq && make -f Makefile.coq'`
- Agent end-to-end: `docker compose -f docker/compose.yaml run --rm agent prove addloop`
- Model server: Ollama on the host (qwen3-coder:30b), reached via host.docker.internal:11434. Already running, GPU-resident.

## Current task (where we are)
Getting `cloq-agent prove addloop` to close the smoke target end-to-end, no LLM.
Everything works EXCEPT the generated theorem's Coq module scope:
- src/cloq_agent/proof/theorem_builder.py renders a .v that instantiates the
  vendored functor (vendor/picinae/timing/examples/riscv_addloop_timing_proof.v).
- pet-server `start` fails: "The reference startof was not found in the current
  environment." `startof` is a Picinae primitive in Picinae_core/Picinae_theory.
- Hypothesis: the generated theorem sits at top level after `Import P NRV32`, but
  `startof`/`models`/`rvtypctx` are only in scope INSIDE Module TimingProof. Fix is
  likely to state the theorem inside a functor mirroring the vendored proof's structure,
  then instantiate. Iterate against pet-server until `start` succeeds, then the gold
  proof script (eval/targets.yaml gold_proof) drives to Qed.

## Key facts learned this session
- _CoqProject uses `-R ../vendor/picinae Picinae` + `-I` for riscv/examples/array dirs.
- vendored Cloq tactic is `hammer` (NOT `whammer` — that name doesn't exist here).
- addloop real addresses are 0x8 (entry) / 0x20 (exit), not 0x0/0x10.
- TargetSpec.name bug: uses lifted_program ("lifted_prog") so file is Lifted_prog_gen.v;
  should be the target key ("addloop"). Fix in eval/targets.py build_spec.
- agent mounts: ..:/app and ../proofs:/work/proofs; workspace=/work/proofs (CLOQ_PETANQUE_WORKSPACE).
- src is bind-mounted, so Python edits are live (clear __pycache__ if stale); .v regenerates each run.
