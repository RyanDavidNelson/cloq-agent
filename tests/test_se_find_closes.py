"""Pin the held-out se_find_{eq,ge} capstones as STANDALONE coqc-Qed `.vo`s.

`proofs/targets/Se_find_eq_gen.v` (==) and `Se_find_ge_gen.v` (>=) are the first held-out search
functions the engine closes end-to-end: a generated program, an emitted (`cloq_`-namespaced)
array-search decidability, an emitted bottom-test `time_of`, and a body invariant — all discharged
by a flat `Proof. ... Qed.` (curly-brace focus + one order-independent `all: solve [..|..|..]` per
loop-body fan-out, NOT numbered positional focus, which does not linearise outside petanque).

se_find_ge is the GENERALISATION sibling: byte-identical program to se_find_eq except the loop's
compare branch (`bltu` vs `bne`), so it proves the decidability template + closer are not
predicate-baked — the delta is the `>=` predicate in the emitted decidability and three closer arms.

"Closes in petanque" and "produces a kernel-checked `.vo`" are different claims; this pins the
second. It compiles each committed `.v` with the project load path and asserts a real Qed (no "Open
proofs remain", a `.vo` on disk) and that the proof rests only on the documented trust basis
(functional extensionality + the NEORV32 latency constants) — no `admit`/proof-specific axiom.

Skipped unless the rocq container (with coqc) is reachable, mirroring the pet-server gate used by
the find_in_array template test.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.lift import intake  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
_ROCQ = "docker-rocq-1"

# (function, generated-theorem module, lifted-program module, theorem name)
_TARGETS = [
    ("se_find_eq", "Se_find_eq_gen", "se_find_eq_lifted", "se_find_eq_timing_gen"),
    ("se_find_ge", "Se_find_ge_gen", "se_find_ge_lifted", "se_find_ge_timing_gen"),
]


def _rocq_up() -> bool:
    try:
        r = subprocess.run(["docker", "exec", _ROCQ, "true"],
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _coqc(target: str, extra: str = "") -> subprocess.CompletedProcess:
    args = " ".join(intake._coqproject_args(_REPO / "proofs"))
    return subprocess.run(
        ["docker", "exec", _ROCQ, "bash", "-lc",
         f"cd /work/proofs && {extra} coqc {args} {target}"],
        capture_output=True, text=True, timeout=900)


@pytest.mark.skipif(not _rocq_up(), reason=f"no rocq container ({_ROCQ})")
@pytest.mark.parametrize("func,gen,lifted,thm", _TARGETS)
def test_se_find_gen_compiles_to_a_real_qed_vo(func, gen, lifted, thm):
    # the generated program functor the theorem Requires must be present first
    assert (_REPO / "proofs" / "targets" / f"{lifted}.v").exists(), f"{lifted}.v missing"
    _coqc(f"targets/{lifted}.v")

    # fresh build of the theorem; a leftover .vo would mask an open proof
    subprocess.run(["docker", "exec", _ROCQ, "bash", "-lc",
                    f"cd /work/proofs && rm -f targets/{gen}.vo"],
                   capture_output=True, timeout=30)
    r = _coqc(f"targets/{gen}.v")
    combined = r.stdout + r.stderr
    assert r.returncode == 0, f"coqc failed:\n{combined[-1500:]}"
    assert "Open proofs remain" not in combined, combined[-1500:]
    assert "incomplete proof" not in combined.lower(), combined[-1500:]

    vo = subprocess.run(["docker", "exec", _ROCQ, "bash", "-lc",
                         f"test -f /work/proofs/targets/{gen}.vo && echo OK"],
                        capture_output=True, text=True, timeout=30)
    assert vo.stdout.strip() == "OK", f"{gen}.vo was not produced"


@pytest.mark.skipif(not _rocq_up(), reason=f"no rocq container ({_ROCQ})")
@pytest.mark.parametrize("func,gen,lifted,thm", _TARGETS)
def test_se_find_gen_rests_only_on_the_documented_trust_basis(func, gen, lifted, thm):
    """`Print Assumptions` must show no `admit`/proof-specific axiom — only functional
    extensionality (Picinae's standard axiom) and the NEORV32 timing-model constants."""
    _coqc(f"targets/{lifted}.v")
    _coqc(f"targets/{gen}.v")
    check = (f"printf '%s\\n' "
             f"'Require Import {gen}.' "
             f"'Import {thm}_Concrete.' "
             f"'Print Assumptions {thm}.' > /tmp/{func}_assum.v &&")
    r = _coqc(f"/tmp/{func}_assum.v", extra=check)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-1500:]
    # an admitted goal surfaces here as an axiom/admit; it must not.
    assert "admit" not in out.lower(), out
    # only the expected trust-basis axioms may appear
    allowed = ("functional_extensionality", "T_inst_latency", "T_data_latency")
    for line in out.splitlines():
        s = line.strip()
        if "." in s and "BinNums" in s:  # an axiom signature line
            assert any(a in line for a in allowed), f"unexpected axiom: {line}"
