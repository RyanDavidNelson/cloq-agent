"""Retrieve analogues for the current proof situation.

Queries are *goal states* (or a CFG description, for invariant synthesis), not keywords. We
retrieve lemmas/definitions to cite and prior invariants/proofs to imitate, separately, because
they play different roles in the prompt.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import RagCfg
from .embeddings import Embedder
from .store import Record, VectorStore


@dataclass
class Retrieved:
    lemmas: list[Record]
    invariants: list[Record]
    proofs: list[Record]

    def as_prompt_context(self) -> str:
        def block(title: str, recs: list[Record]) -> str:
            if not recs:
                return ""
            body = "\n\n".join(f"(* {r.meta.get('name', r.id)} *)\n{r.text}" for r in recs)
            return f"### {title}\n{body}\n"

        return "\n".join(
            b
            for b in (
                block("Relevant lemmas / definitions", self.lemmas),
                block("Analogous invariant sets", self.invariants),
                block("Analogous completed proofs", self.proofs),
            )
            if b
        )


class Retriever:
    def __init__(self, cfg: RagCfg):
        self.cfg = cfg
        self.embedder = Embedder(cfg)
        self.store = VectorStore.load(cfg.store_dir, self.embedder.dim)

    def retrieve(self, query: str) -> Retrieved:
        q = self.embedder.embed_one(query)
        lemmas = [r for r, _ in self.store.query(q, self.cfg.top_k_lemmas, kind="lemma")]
        defs = [r for r, _ in self.store.query(q, self.cfg.top_k_lemmas, kind="definition")]
        invs = [r for r, _ in self.store.query(q, self.cfg.top_k_proofs, kind="invariant")]
        proofs = [r for r, _ in self.store.query(q, self.cfg.top_k_proofs, kind="proof")]
        return Retrieved(lemmas=lemmas + defs, invariants=invs, proofs=proofs)
