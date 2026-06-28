(* Addloop.v — pipeline smoke target (Option A).

   Instantiates the vendored, machine-checked addloop timing proof
   (vendor/picinae/timing/examples/riscv_addloop_timing_proof.v) for the
   NEORV32 base config. The functor instantiation forces Coq to check the
   proof end-to-end against a concrete CPU. No LLM in the loop.

   Instantiation pattern copied verbatim from the vendored crypto proofs,
   e.g. examples/crypto/ct_swap/ct_swap_proof.v (the NRV32 / TimingProof
   three-liner). This file doubles as the gold target for the eval harness. *)

Require Import NEORV32.
Require Import riscv_addloop_timing_proof.

Module NRV32 := NEORV32 NEORV32BaseConfig.
Module NEORV32TimingProof := TimingProof NRV32.
Import NEORV32TimingProof NRV32.
