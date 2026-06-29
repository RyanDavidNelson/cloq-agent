"""The orchestrator persists a solved proof into the RAG corpus, and it shows up on next retrieve.

This is the Phase 3.3 acceptance: a success is written to the mounted rag_store/ and appears the
next time the store is loaded and queried.
"""
from __future__ import annotations

from cloq_agent.agent.orchestrator import Orchestrator
from cloq_agent.config import Config
from cloq_agent.proof.theorem_builder import TargetSpec
from cloq_agent.rag.embeddings import Embedder
from cloq_agent.rag.store import VectorStore


class _Retriever:
    """Minimal stand-in exposing the .store / .embedder the store-back uses."""
    def __init__(self, embedder, store):
        self.embedder = embedder
        self.store = store


def _orchestrator_with_store(tmp_path):
    cfg = Config()
    cfg.rag.embedder = "hash"                       # offline, deterministic — no model download
    cfg.rag.store_dir = str(tmp_path / "rag_store")
    emb = Embedder(cfg.rag)
    orch = Orchestrator.__new__(Orchestrator)       # skip heavy __init__ (model/retriever/workspace)
    orch.cfg = cfg
    orch.retriever = _Retriever(emb, VectorStore(emb.dim))
    return orch, cfg, emb


def test_store_solved_persists_to_volume_and_is_retrievable(tmp_path):
    orch, cfg, emb = _orchestrator_with_store(tmp_path)
    spec = TargetSpec(
        name="sum3", requires=[], lifted_program="lifted_prog", entry_addr=0,
        exit_point="exits", theorem_name="sum3_timing_gen", params=[],
    )
    invariant = ("Definition sum3_timing_invs (t:trace) := "
                 "(* cycle_count_of_trace t' = tadd + tadd *) timing.")
    ok = orch._store_solved(spec, invariant, ["intros.", "now step.", "Qed."])
    assert ok is True

    # Persisted to the mounted store directory.
    assert (tmp_path / "rag_store" / "records.jsonl").exists()
    assert (tmp_path / "rag_store" / "vecs.npy").exists()

    # Appears on the NEXT retrieve via the real Retriever (fresh load of the same store dir).
    from cloq_agent.rag.retriever import Retriever

    retrieved = Retriever(cfg.rag).retrieve("sum3 timing cycle_count closed form")
    assert any(r.meta.get("target") == "sum3" for r in retrieved.proofs), \
        "solved proof not surfaced on next retrieve"
    rec = next(r for r in retrieved.proofs if r.meta.get("target") == "sum3")
    assert rec.kind == "proof"
    assert "sum3_timing_invs" in rec.text and "Qed." in rec.text


def test_store_solved_is_best_effort_on_error(tmp_path):
    orch, cfg, emb = _orchestrator_with_store(tmp_path)
    # A broken embedder must not raise out of store-back (a corpus write can't fail a sound proof).
    orch.retriever.embedder = object()
    spec = TargetSpec(name="f", requires=[], lifted_program="lifted_prog", entry_addr=0,
                      exit_point="exits", theorem_name="f_timing_gen", params=[])
    assert orch._store_solved(spec, "Definition f_invs := x.", ["Qed."]) is False
