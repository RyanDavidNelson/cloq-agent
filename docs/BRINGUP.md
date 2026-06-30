# Bring-up checklist

> **Status:** Track A (software / proof engine) is the **live** path and is well past bring-up —
> follow it to get `addloop` proving. Track B (FPGA) is **deferred / parked**; it is retained below
> as design intent only and has **no dependency** in the build/run/CI/GUI. The current deliverable is
> proof-only (closed form + predicted range), so the "convergence" milestone (measured == predicted)
> does not apply today. See `docs/RESULTS.md`.

Track A (software) has no board dependency. The first software milestone is `addloop` proven
end-to-end with no LLM.

## Track A — software / proof engine (workstation)

1. `git submodule update --init --recursive` to populate `vendor/picinae`.
2. `docker compose -f docker/compose.yaml up -d rocq` — brings up Rocq + `pet-server` (petanque)
   with Picinæ/Cloq built.
3. Reconcile `proofs/targets/Addloop.v` identifiers with your vendored Cloq (module names,
   `satisfies_all`, `cycle_count`, `whammer`, the lifted `addloop` map). Then `make -C proofs smoke`.
4. `ollama pull qwen3-coder:30b` (workhorse on the 5090) and confirm `cloq-agent index` runs.
5. `cloq-agent prove addloop` — should close via the gold invariant + hammer ladder, no LLM.

## Track B — FPGA oracle (AUP-ZU3)  *(DEFERRED — parked, do not run for the current deliverable)*

1. Install **Vivado/Vitis** (free Standard edition is fine for the XCZU3EG) and the **AUP-ZU3
   board files** from RealDigital. Confirm the exact part/speed-grade string from the board files.
2. Package NEORV32 as Vivado IP: in `neorv32/rtl/system_integration`, Tcl-console
   `source neorv32_vivado_ip.tcl`.
3. `vivado -mode batch -source fpga/vivado/build_neorv32_zynq.tcl -tclargs <neorv32_repo>` to get a
   bitstream + `.xsa`.
4. Flash a trivial counting-loop firmware and read its cycle count back over AXI from Python — your
   "hello, mcycle". This proves the PS↔PL↔measurement path before any real target.
5. `make -C fpga/firmware TARGET_OBJ=addloop.o`, then on the board
   `python fpga/host/measure.py --target addloop --sweep-inputs 64`.

## Convergence — the first milestone  *(parked: needs Track B)*

> Deferred with Track B. For the current proof-only deliverable the milestone is simply `addloop`
> proven in Track A with the CFG-derived closed form `(x)·(ft+2+2+tb)`; the hardware cross-check
> below is parked.

`addloop` proves in Track A and measures in Track B, and the measured cycles match the Cloq
closed form `(x)·(ft+2+2+tb)` across the input sweep. From here, everything (RAG-driven synthesis,
ct-swap, ChaCha20, mutation testing, the ablation study) is scaling this one vertical slice.

## Order of attack on targets

`addloop` → `vlist_insert_end` (and the rest of the list.c easy set) → `ct_swap` (first crypto;
20-min expert proof) → `chacha20_block` (primary crypto demo; gold = 13624 cycles) → stretch:
`vListInsert` (cyclic-list uniqueness) and a second ISA timing module.
