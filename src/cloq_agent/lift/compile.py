"""Compile an uploaded C unit to a RISC-V object + disassembly (the `compile` pipeline stage).

This is the front door of the C-intake path: a self-contained C file in, a RV32 object file and
its `objdump -d` listing out, with the toolchain version and exact flags recorded for provenance.
Nothing here is trusted by the prover — it only produces the bytes the lifter turns into a Picinæ
program; soundness still comes from Rocq.

PINNED FLAGS / the matched-pair rule (CLAUDE.md golden rule #4). The cycle counts are only
meaningful against the *exact* instruction stream the NEORV32 timing model was calibrated on, so
the flags and the timing model are one unit:

  -march=rv32im_zicsr_zicntr  -mabi=ilp32  -O2  -ffreestanding  -nostdlib

Two deliberate choices worth calling out:
  * `-O2` is the pinned optimization level; it fixes instruction selection, hence the timing
    model. Changing it re-derives every predicted cycle count — do not vary it casually.
  * `rv32im` is NON-compressed on purpose. The task brief writes `rv32imc`, but the vendored
    NEORV32 timing model (vendor/.../riscv/RVCPUTimingBehavior.v) defines per-instruction times
    only for full 32-bit RV32IM instructions — there are no `c.*` timings — and `lift/cfg.py`'s
    timing table is likewise non-compressed. Emitting compressed instructions would break the
    flags<->model pair and the CFG timing derivation. `_zicsr_zicntr` keeps the cycle-counter
    CSRs available (needs binutils >= 2.41; see docker/Dockerfile.toolchain).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .cfg import parse_objdump

# The pinned optimization level, surfaced as a named constant so the report and any caller can
# record it next to the predicted cycle count.
OPT_LEVEL = "-O2"
MARCH = "rv32im_zicsr_zicntr"
MABI = "ilp32"
DEFAULT_CFLAGS: tuple[str, ...] = (
    f"-march={MARCH}",
    f"-mabi={MABI}",
    OPT_LEVEL,
    "-ffreestanding",
    "-nostdlib",
)

# Tool names; overridable for a differently-prefixed cross toolchain (e.g. riscv-none-elf-*).
GCC = os.environ.get("CLOQ_RISCV_GCC", "riscv64-unknown-elf-gcc")
OBJDUMP = os.environ.get("CLOQ_RISCV_OBJDUMP", "riscv64-unknown-elf-objdump")

# Written into the toolchain image by docker/Dockerfile.toolchain.
_VERSION_FILE = Path("/etc/cloq-toolchain-version")


@dataclass
class CompileResult:
    """Outcome of the `compile` stage. `ok` gates the rest of the pipeline; `stderr` is surfaced
    verbatim into the report's compile stage so a user sees the real gcc diagnostic."""
    ok: bool
    func: str
    flags: list[str]
    obj_path: Path | None = None
    objdump: str | None = None
    toolchain_version: str | None = None
    stderr: str = ""
    error: str | None = None


def toolchain_version(gcc: str = GCC, objdump: str = OBJDUMP) -> str | None:
    """The pinned toolchain version string for provenance. Prefer the value baked into the image;
    fall back to querying the binaries; None if the toolchain isn't installed."""
    if _VERSION_FILE.exists():
        return _VERSION_FILE.read_text().strip()
    out = []
    for tool in (gcc, objdump):
        if shutil.which(tool):
            try:
                v = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=20)
                out.append(v.stdout.splitlines()[0] if v.stdout else "")
            except (OSError, subprocess.SubprocessError):
                return None
    return "\n".join(out) if out else None


def disassemble(obj_path: Path, objdump: str = OBJDUMP) -> str:
    """`objdump -d -M no-aliases` of an object — the listing `lift/cfg.py:parse_objdump` consumes.
    `no-aliases` keeps canonical mnemonics (jalr, not ret) so the timing table matches."""
    r = subprocess.run(
        [objdump, "-d", str(obj_path), "-M", "no-aliases"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        raise RuntimeError(f"objdump failed: {r.stderr.strip()}")
    return r.stdout


def compile_c(
    c_path: str | Path,
    func: str,
    *,
    flags: list[str] | tuple[str, ...] | None = None,
    workdir: str | Path | None = None,
    gcc: str = GCC,
    objdump: str = OBJDUMP,
) -> CompileResult:
    """Compile `c_path` to a RV32 object and disassemble it.

    `func` is the entry function of interest (used to name artifacts and, later, to pick the entry
    address); a self-contained unit should define it as a global symbol. Compiles with `-c` (no
    link) because a `-nostdlib` unit has no `_start`; the single function lands in `.text` and the
    lifter consumes the whole object. Returns a CompileResult whose `ok=False` carries gcc's stderr
    instead of raising, so the caller can render a clean `compile`-stage failure.
    """
    c_path = Path(c_path)
    flags = list(flags) if flags is not None else list(DEFAULT_CFLAGS)
    version = toolchain_version(gcc, objdump)

    if not c_path.exists():
        return CompileResult(False, func, flags, error=f"no such C file: {c_path}")
    if not shutil.which(gcc):
        return CompileResult(False, func, flags, toolchain_version=version,
                             error=f"RISC-V gcc '{gcc}' not on PATH (build docker/Dockerfile.toolchain)")

    workdir = Path(workdir) if workdir else c_path.resolve().parent
    workdir.mkdir(parents=True, exist_ok=True)
    obj_path = workdir / f"{c_path.stem}.o"

    cmd = [gcc, *flags, "-c", str(c_path), "-o", str(obj_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        return CompileResult(False, func, flags, toolchain_version=version, stderr=proc.stderr,
                             error=f"gcc exited {proc.returncode}")

    try:
        listing = disassemble(obj_path, objdump)
    except RuntimeError as e:
        return CompileResult(False, func, flags, obj_path=obj_path, toolchain_version=version,
                             stderr=proc.stderr, error=str(e))

    if f"<{func}>:" not in listing:
        return CompileResult(False, func, flags, obj_path=obj_path, objdump=listing,
                             toolchain_version=version, stderr=proc.stderr,
                             error=f"function '{func}' not found in the compiled object")

    return CompileResult(True, func, flags, obj_path=obj_path, objdump=listing,
                         toolchain_version=version, stderr=proc.stderr)


def sanitize_ident(name: str) -> str:
    """Turn an arbitrary upload name into a valid Coq/identifier stem for the generated scaffolding
    (the program/theorem modules). Non-ident chars become `_`; a leading digit is prefixed."""
    out = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_") or "program"
    return f"p_{out}" if out[0].isdigit() else out


def load_machine_code(path: str | Path, func: str | None = None, objdump: str = OBJDUMP) -> CompileResult:
    """Disassemble an uploaded machine-code artifact (ELF / object) for the lift stage — the
    no-compile intake. There is no source and no compiler here, so `flags` is empty and the program
    name is derived from `func` (if given) or the file name. The whole artifact is lifted; a per-
    function entry/exit is recovered by the CFG, so no source-level function selection is required.
    """
    path = Path(path)
    name = sanitize_ident(func or path.stem)
    version = toolchain_version(objdump=objdump)

    if not path.exists():
        return CompileResult(False, name, [], error=f"no such file: {path}")
    if not shutil.which(objdump):
        return CompileResult(False, name, [], toolchain_version=version,
                             error=f"objdump '{objdump}' not on PATH (build docker/Dockerfile.toolchain)")
    try:
        listing = disassemble(path, objdump)
    except RuntimeError as e:
        return CompileResult(False, name, [], obj_path=path, toolchain_version=version,
                             error=f"could not disassemble the upload (expected an ELF/object): {e}")
    if not parse_objdump(listing):
        return CompileResult(False, name, [], obj_path=path, objdump=listing,
                             toolchain_version=version,
                             error="no instructions found — is this RISC-V machine code?")
    return CompileResult(True, name, [], obj_path=path, objdump=listing, toolchain_version=version)
