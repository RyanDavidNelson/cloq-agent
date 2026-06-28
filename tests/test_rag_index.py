"""The corpus indexes vendored EXAMPLE proof bodies so the T4 memory-aliasing closers are
retrievable as prior-proof context (Task 5, point 3).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent.rag.index import _solved_proof_records

_EXAMPLES = Path(__file__).resolve().parents[1] / "vendor" / "picinae" / "timing" / "examples"

pytestmark = pytest.mark.skipif(
    not _EXAMPLES.exists(), reason="vendored picinae examples not present"
)


def test_example_proof_bodies_carry_t4_closers():
    # Same cap build_index applies to examples; the default 4000 would cut getmem_noverlap.
    recs = _solved_proof_records(_EXAMPLES, max_chars=12000)
    assert recs, "no example proof records indexed"
    assert all(r.kind == "proof" for r in recs)
    corpus = "\n".join(r.text for r in recs)
    # The T4 closers live in the proof BODIES (uxListRemove.v etc.), so indexing whole bodies
    # is what makes them retrievable — a decl-header sweep would miss them.
    for closer in ("preserve_noverlaps", "getmem_noverlap", "find_rewrites", "noverlap_symmetry"):
        assert closer in corpus, f"{closer} not retrievable from indexed example proofs"
