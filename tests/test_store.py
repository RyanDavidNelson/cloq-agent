"""Vector store round-trip + cosine query."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
from cloq_agent.rag.store import Record, VectorStore


def _rec(i, kind="lemma"):
    return Record(id=f"r{i}", text=f"text {i}", kind=kind, meta={"name": f"n{i}"})


def test_add_and_query_orders_by_cosine():
    s = VectorStore(dim=3)
    s.add(_rec(0), np.array([1, 0, 0], dtype="float32"))
    s.add(_rec(1), np.array([0, 1, 0], dtype="float32"))
    hits = s.query(np.array([0.9, 0.1, 0], dtype="float32"), k=2)
    assert hits[0][0].id == "r0"
    assert len(hits) == 2


def test_kind_filter():
    s = VectorStore(dim=2)
    s.add(_rec(0, "lemma"), np.array([1, 0], dtype="float32"))
    s.add(_rec(1, "proof"), np.array([1, 0], dtype="float32"))
    hits = s.query(np.array([1, 0], dtype="float32"), k=5, kind="proof")
    assert all(r.kind == "proof" for r, _ in hits)


def test_save_load_roundtrip(tmp_path):
    s = VectorStore(dim=2)
    s.add(_rec(0), np.array([1, 0], dtype="float32"))
    s.save(tmp_path)
    s2 = VectorStore.load(tmp_path, dim=2)
    assert len(s2) == 1 and s2._records[0].id == "r0"
