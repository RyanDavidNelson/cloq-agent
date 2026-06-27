"""Ablation study: reproduce the Rango-style finding on our own targets.

Toggles three levers and re-runs the eval, so we can report a table like Rango's retriever
ablation (retrieval is worth ~10+ absolute points there). This is the most interview-legible
artifact the project produces, and it is honest science: it measures what actually helps.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from cloq_agent.config import Config

from .harness import run_eval, EvalReport


@dataclass
class Ablation:
    name: str
    mutate: str   # human description of what is disabled


ABLATIONS = [
    Ablation("full", "everything on"),
    Ablation("no_rag", "retrieval disabled (top_k -> 0)"),
    Ablation("no_hammer_first", "skip the automation ladder; LLM-only"),
    Ablation("local_only", "escalation disabled"),
]


def _apply(cfg: Config, ab: Ablation) -> Config:
    c = copy.deepcopy(cfg)
    if ab.name == "no_rag":
        c.rag.top_k_lemmas = 0
        c.rag.top_k_proofs = 0
    elif ab.name == "no_hammer_first":
        c.agent.hammer_first = False
    elif ab.name == "local_only":
        c.model.escalation.base_url = None
        c.model.escalation.name = None
    return c


def run_ablations(cfg: Config, repo_root, only=None) -> dict[str, EvalReport]:
    out: dict[str, EvalReport] = {}
    for ab in ABLATIONS:
        out[ab.name] = run_eval(_apply(cfg, ab), repo_root, only=only)
    return out


def summarize(reports: dict[str, "EvalReport"]) -> dict[str, float]:
    return {
        name: sum(r.proved for r in rep.results) / max(len(rep.results), 1)
        for name, rep in reports.items()
    }
