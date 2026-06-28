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
DONE: `cloq-agent prove addloop` closes the smoke target end-to-end, no LLM
(`PROVED addloop iters=12 llm_calls=0`). The gold proof (eval/targets.yaml
gold_proof) drives the functor-structured generated theorem to Qed via petanque.

Root cause of the old "startof was not found" was NOT module scope — it was
that pet-server was launched without `--root`, so coq-lsp never loaded
/work/proofs/_CoqProject and `Require Import Picinae_riscv` silently failed
(only the stdlib was on the loadpath; `N` resolved, every Picinae symbol did
not). Fixed in docker/Dockerfile.rocq: `pet-server ... --root /work/proofs`.
Diagnose loadpath issues with `fcc --root=/work/proofs targets/<f>.v` (coq-lsp's
CLI checker) — it shares coq-lsp's _CoqProject discovery, unlike `coqc`.

Other fixes this task:
- theorem_builder.py: after `Import Inner` (Inner := TimingProof cpu), also
  `Import Inner.RISCVTiming/Program_addloop/addloopAuto` — `Import Inner` alone
  does NOT expose the functor outputs (lifted_prog/exits/entry_addr/
  cycle_count_of_trace/t* constants); also dropped the stray `Qed.` after the
  `Admitted.` placeholder (kept the theorem inside `Module {thm}_Proof (cpu)`).
- cli.py: pass `name=args.target` to build_spec (was defaulting to lifted_prog).
- eval/targets.yaml: gold_invariant path had one extra `../` (resolved outside
  the repo → None → silent LLM fallback); now `../vendor/...`.
- orchestrator.py: imported `run_script` from proof.hammer (gold path called it
  but it was never imported → NameError once `start` started succeeding).

## Key facts learned this session
- _CoqProject uses `-R ../vendor/picinae Picinae` + `-I` for riscv/examples/array dirs.
- vendored Cloq tactic is `hammer` (NOT `whammer` — that name doesn't exist here).
- addloop real addresses are 0x8 (entry) / 0x20 (exit), not 0x0/0x10.
- TargetSpec.name: build_spec falls back to lifted_program ("lifted_prog") unless
  passed name=; cli.cmd_prove now passes name=args.target so file is Addloop_gen.v.
- agent mounts: ..:/app and ../proofs:/work/proofs; workspace=/work/proofs (CLOQ_PETANQUE_WORKSPACE).
- src is bind-mounted, so Python edits are live (clear __pycache__ if stale); .v regenerates each run.
