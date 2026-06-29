# Held-out transfer suite — pinned sources

Functions are reduced to self-contained compilable units under `<suite>/src/` (verbatim function
bodies from the pinned trees, with a minimal typedef/macro shim instead of the full build machinery).
The reduction preserves the real instruction-selection-relevant code; held-out discipline (the
target's gold invariant/proof is withheld from `load_proof_library` and the few-shot) is enforced by
the harness, so a pass measures generalization, not recall.

| suite | upstream | tag | commit |
|---|---|---|---|
| openssl  | github.com/openssl/openssl            | openssl-3.4.0 | 98acb6b02839c609ef5b837794e08d906d965335 |
| freertos | github.com/FreeRTOS/FreeRTOS-Kernel    | V11.1.0       | dbf70559b27d39c1fdb68dfb9a32140b6a6777a0 |

Pinned compile flags (matched to the NEORV32 timing model): `-march=rv32im_zicsr_zicntr -mabi=ilp32
-O2 -ffreestanding -nostdlib` (see `src/cloq_agent/lift/compile.py`).
