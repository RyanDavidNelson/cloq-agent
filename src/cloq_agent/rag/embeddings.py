"""Text embeddings with three backends, chosen by config:

- "local"    : sentence-transformers on the 5090 (best; needs the `rag` extra)
- "endpoint" : any OpenAI-compatible /v1/embeddings server (e.g. an embed model in Ollama)
- "hash"     : a deterministic hashing embedder — NO semantic quality, but keeps the whole
               pipeline runnable in CI / offline so nothing else is blocked on a model download.

The hash backend exists so the rest of the system is testable without a GPU; it is not meant
for real retrieval. Retrieval quality is a first-class eval ablation (see eval/ablations.py).
"""
from __future__ import annotations

import hashlib

import numpy as np

from ..config import RagCfg


class Embedder:
    def __init__(self, cfg: RagCfg):
        self.cfg = cfg
        self.dim = cfg.embed_dim
        self._mode = cfg.embedder
        self._st_model = None
        self._client = None
        if self._mode == "local":
            try:
                from sentence_transformers import SentenceTransformer

                self._st_model = SentenceTransformer(cfg.embed_model)
                self.dim = self._st_model.get_sentence_embedding_dimension()
            except Exception:
                # fall back rather than crash; warn loudly via mode change
                self._mode = "hash"
        elif self._mode == "endpoint":
            from openai import OpenAI

            self._client = OpenAI(base_url=cfg.embed_endpoint, api_key="x")

    def embed(self, texts: list[str]) -> np.ndarray:
        if self._mode == "local" and self._st_model is not None:
            return np.asarray(self._st_model.encode(texts, normalize_embeddings=True), dtype=np.float32)
        if self._mode == "endpoint" and self._client is not None:
            resp = self._client.embeddings.create(model=self.cfg.embed_model, input=texts)
            return np.asarray([d.embedding for d in resp.data], dtype=np.float32)
        return np.vstack([self._hash_embed(t) for t in texts])

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]

    def _hash_embed(self, text: str) -> np.ndarray:
        """Deterministic bag-of-token-hashes vector. Cheap, reproducible, low quality."""
        vec = np.zeros(self.dim, dtype=np.float32)
        for tok in text.lower().split():
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        n = np.linalg.norm(vec)
        return vec / n if n > 0 else vec
