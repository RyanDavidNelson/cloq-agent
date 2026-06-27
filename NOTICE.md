# NOTICE

This project builds tooling on top of third-party formal verification,
binary-analysis, and hardware/software components. Each third-party component
remains under its own license and copyright terms.

## Third-party components

- Picinæ / Picinae and Cloq — `vendor/picinae`
  - Picinae binary-analysis framework and Cloq timing-proof layer.
  - Picinae and Cloq are Copyright (c) 2025 Kevin W. Hamlen and Charles Averill
    (The University of Texas at Dallas, Computer Science Dept.).
  - Their terms state that use requires the authors' express permission; that
    permission was granted for this project.
  - Original copyright headers are preserved.
  - Do not reuse, redistribute, or separately incorporate the vendored/referenced
    Picinae/Cloq code without contacting the authors and verifying permission.

<<<<<<< HEAD
All files outside `vendor/` are Copyright (c) 2026 Ryan Nelson, MIT License (see LICENSE).
=======
- coq-lsp / petanque — Emilio J. Gallego Arias and contributors, LGPL-2.1
- pytanque — LLM4Rocq
- coqpyt — sr-lab
- CoqHammer — respective authors
- Tactician — respective authors
- NEORV32 — Stephan Nolting, BSD-3-Clause

## References

- C. Averill, "Formally-Verified, Tight Timing Constraints for Machine Code,"
  PLDI SRC '25.
- K. W. Hamlen et al., Picinae: Platform In Coq for INstruction Analysis of
  Executables.

## Project license

All files outside `vendor/` are Copyright (c) 2026 Ryan Nelson and are
MIT-licensed, unless otherwise stated in an individual file header.
See `LICENSE`.
>>>>>>> 9365c84 (Rough Draft)
