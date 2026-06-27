"""Build the retrieval corpus.

Two sources, per the Rango finding that retrieving *both* lemmas and prior proofs is what
moves the needle:

1. The Picinæ/Cloq theory libraries (`vendor/picinae/**/*.v`): every Lemma/Theorem/Definition
   signature becomes a "lemma" record — these are the building blocks the LLM should cite.
2. Solved proofs (`proofs/targets/*.v` that end in `Qed`, plus a growing `runs/` library): each
   completed invariant set + proof script becomes a "proof"/"invariant" record — analogues the
   LLM adapts for new targets.

Extraction uses coqpyt (AST-accurate) when installed; otherwise a regex fallback that is good
enough to bootstrap. Either way the goal is signatures + bodies, not whole-file blobs.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..config import RagCfg
from .embeddings import Embedder
from .store import Record, VectorStore

_DECL = re.compile(
    r"^\s*(Lemma|Theorem|Corollary|Definition|Fixpoint)\s+([A-Za-z0-9_']+)",
    re.MULTILINE,
)
_INVARIANT = re.compile(r"Definition\s+(\w*timing_invs\w*|\w*_invs)\b", re.IGNORECASE)


def _extract_decls(text: str, source: str) -> list[Record]:
    """Regex fallback: pull declaration headers with a few lines of context."""
    records: list[Record] = []
    lines = text.splitlines()
    for m in _DECL.finditer(text):
        kind_kw, name = m.group(1), m.group(2)
        start = text[: m.start()].count("\n")
        snippet = "\n".join(lines[start : start + 8])
        kind = "definition" if kind_kw in ("Definition", "Fixpoint") else "lemma"
        if _INVARIANT.search(snippet):
            kind = "invariant"
        records.append(
            Record(
                id=f"{source}::{name}",
                text=snippet,
                kind=kind,
                meta={"name": name, "source": source, "decl": kind_kw},
            )
        )
    return records


def _extract_with_coqpyt(path: Path) -> list[Record] | None:
    """Try AST-accurate extraction. Returns None if coqpyt is unavailable so callers fall back."""
    try:
        from coqpyt.coq.base_file import CoqFile  # type: ignore
    except Exception:
        return None
    records: list[Record] = []
    try:
        with CoqFile(str(path)) as cf:  # type: ignore
            cf.run()
            for step in cf.steps:
                txt = getattr(step, "text", "").strip()
                m = _DECL.match(txt)
                if not m:
                    continue
                kind_kw, name = m.group(1), m.group(2)
                kind = "definition" if kind_kw in ("Definition", "Fixpoint") else "lemma"
                if _INVARIANT.search(txt):
                    kind = "invariant"
                records.append(
                    Record(
                        id=f"{path}::{name}",
                        text=txt[:1200],
                        kind=kind,
                        meta={"name": name, "source": str(path), "decl": kind_kw},
                    )
                )
    except Exception:
        return None
    return records


def _solved_proof_records(proof_dir: Path) -> list[Record]:
    """Index completed proofs (files containing Qed) as reusable analogues."""
    records: list[Record] = []
    for p in proof_dir.rglob("*.v"):
        text = p.read_text(errors="ignore")
        if "Qed." not in text:
            continue
        records.append(
            Record(
                id=f"solved::{p.name}",
                text=text[:4000],
                kind="proof",
                meta={"source": str(p)},
            )
        )
    return records


def build_index(cfg: RagCfg, *, vendor: Path, proofs: Path, runs: Path | None = None) -> VectorStore:
    embedder = Embedder(cfg)
    store = VectorStore(embedder.dim)

    records: list[Record] = []
    for vfile in sorted(Path(vendor).rglob("*.v")):
        recs = _extract_with_coqpyt(vfile)
        if recs is None:
            recs = _extract_decls(vfile.read_text(errors="ignore"), str(vfile))
        records.extend(recs)

    records.extend(_solved_proof_records(Path(proofs)))
    if runs is not None and Path(runs).exists():
        records.extend(_solved_proof_records(Path(runs)))

    if records:
        vecs = embedder.embed([r.text for r in records])
        for rec, vec in zip(records, vecs):
            store.add(rec, vec)
    store.save(cfg.store_dir)
    return store
