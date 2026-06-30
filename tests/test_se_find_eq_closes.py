"""Pin the held-out se_find_eq capstone as a STANDALONE coqc-Qed `.vo`.

`proofs/targets/Se_find_eq_gen.v` is the first held-out search function the engine closes
end-to-end: a generated program, an emitted (`cloq_`-namespaced) array-search decidability, an
emitted bottom-test `time_of`, and a body invariant — all discharged by a flat
`Proof. ... Qed.` (curly-brace focus + one order-independent `all: solve [..|..|..]` per loop-body
fan-out, NOT numbered positional focus, which does not linearise outside petanque).

"Closes in petanque" and "produces a kernel-checked `.vo`" are different claims; this test pins the
second. It compiles the committed `.v` with the project load path and asserts a real Qed (no "Open
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
_GEN = "targets/Se_find_eq_gen.v"
_LIFTED = "targets/se_find_eq_lifted.v"


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
def test_se_find_eq_gen_compiles_to_a_real_qed_vo():
    # the generated program functor the theorem Requires must be present first
    assert (_REPO / "proofs" / _LIFTED).exists(), "se_find_eq_lifted.v missing"
    _coqc(_LIFTED)

    # fresh build of the theorem; a leftover .vo would mask an open proof
    subprocess.run(["docker", "exec", _ROCQ, "bash", "-lc",
                    "cd /work/proofs && rm -f targets/Se_find_eq_gen.vo"],
                   capture_output=True, timeout=30)
    r = _coqc(_GEN)
    combined = r.stdout + r.stderr
    assert r.returncode == 0, f"coqc failed:\n{combined[-1500:]}"
    assert "Open proofs remain" not in combined, combined[-1500:]
    assert "incomplete proof" not in combined.lower(), combined[-1500:]

    vo = subprocess.run(["docker", "exec", _ROCQ, "bash", "-lc",
                         "test -f /work/proofs/targets/Se_find_eq_gen.vo && echo OK"],
                        capture_output=True, text=True, timeout=30)
    assert vo.stdout.strip() == "OK", "Se_find_eq_gen.vo was not produced"


@pytest.mark.skipif(not _rocq_up(), reason=f"no rocq container ({_ROCQ})")
def test_se_find_eq_gen_rests_only_on_the_documented_trust_basis():
    """`Print Assumptions` must show no `admit`/proof-specific axiom — only functional
    extensionality (Picinae's standard axiom) and the NEORV32 timing-model constants."""
    _coqc(_LIFTED)
    _coqc(_GEN)
    check = ("printf '%s\\n' "
             "'Require Import Se_find_eq_gen.' "
             "'Import se_find_eq_timing_gen_Concrete.' "
             "'Print Assumptions se_find_eq_timing_gen.' > /tmp/se_find_eq_assum.v &&")
    r = _coqc("/tmp/se_find_eq_assum.v", extra=check)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-1500:]
    lowered = out.lower()
    # an admitted goal surfaces here as an axiom/admit; it must not.
    assert "admit" not in lowered, out
    # only the expected trust-basis axioms may appear
    allowed = ("functional_extensionality", "T_inst_latency", "T_data_latency")
    for line in out.splitlines():
        s = line.strip()
        if "." in s and "BinNums" in s:  # an axiom signature line
            assert any(a in line for a in allowed), f"unexpected axiom: {line}"
