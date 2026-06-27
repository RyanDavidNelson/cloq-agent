"""A deliberately tiny, transparent vector store (JSONL + numpy cosine).

Why not chromadb/faiss? For a few thousand lemma/proof records the brute-force cosine search
is sub-millisecond, has zero opaque state, and keeps the corpus diff-able in git. The interface
(`add`, `query`, `save`, `load`) is a drop-in for a real ANN store later if the library grows.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np


@dataclass
class Record:
    id: str
    text: str
    kind: str                 # "lemma" | "proof" | "invariant" | "definition"
    meta: dict


class VectorStore:
    def __init__(self, dim: int):
        self.dim = dim
        self._records: list[Record] = []
        self._vecs: np.ndarray = np.zeros((0, dim), dtype=np.float32)

    def __len__(self) -> int:
        return len(self._records)

    def add(self, record: Record, vec: np.ndarray) -> None:
        v = np.asarray(vec, dtype=np.float32).reshape(1, -1)
        if v.shape[1] != self.dim:
            raise ValueError(f"embedding dim {v.shape[1]} != store dim {self.dim}")
        self._records.append(record)
        self._vecs = np.vstack([self._vecs, v]) if len(self._vecs) else v

    def query(
        self, vec: np.ndarray, k: int, kind: str | None = None
    ) -> list[tuple[Record, float]]:
        if len(self._records) == 0:
            return []
        q = np.asarray(vec, dtype=np.float32).reshape(-1)
        mat = self._vecs
        denom = (np.linalg.norm(mat, axis=1) * np.linalg.norm(q)) + 1e-9
        sims = (mat @ q) / denom
        order = np.argsort(-sims)
        out: list[tuple[Record, float]] = []
        for i in order:
            rec = self._records[int(i)]
            if kind is not None and rec.kind != kind:
                continue
            out.append((rec, float(sims[int(i)])))
            if len(out) >= k:
                break
        return out

    # --- persistence -----------------------------------------------------

    def save(self, store_dir: str | Path) -> None:
        d = Path(store_dir)
        d.mkdir(parents=True, exist_ok=True)
        with (d / "records.jsonl").open("w") as f:
            for r in self._records:
                f.write(json.dumps(asdict(r)) + "\n")
        np.save(d / "vecs.npy", self._vecs)

    @classmethod
    def load(cls, store_dir: str | Path, dim: int) -> "VectorStore":
        d = Path(store_dir)
        store = cls(dim)
        recs_path, vecs_path = d / "records.jsonl", d / "vecs.npy"
        if recs_path.exists() and vecs_path.exists():
            store._records = [Record(**json.loads(line)) for line in recs_path.read_text().splitlines()]
            store._vecs = np.load(vecs_path)
        return store
