"""Proof-engine control via petanque (the proof API that ships inside coq-lsp).

We do NOT talk to Rocq directly and we do NOT spawn coqtop. petanque exposes a low-latency,
gym-like interface (start / run-tactic / read-goals) over TCP, and `pytanque` is its Python
client. This module is a thin, typed wrapper that the orchestrator drives.

Run the server first (inside the rocq container):

    pet-server --address 0.0.0.0 --port 8765
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

try:
    from pytanque import Pytanque  # type: ignore
except Exception:  # pragma: no cover - import guard for environments without pytanque
    Pytanque = None  # type: ignore

from ..config import PetanqueCfg


@dataclass
class Goal:
    pretty: str          # human/LLM-readable pretty-print of the goal
    hypotheses: list[str]
    conclusion: str


@dataclass
class StepResult:
    ok: bool
    finished: bool
    goals: list[Goal]
    error: str | None
    state: object | None  # opaque petanque state handle to thread into the next run()


class PetanqueDriver:
    """One connection to a running pet-server, scoped to a single Rocq workspace."""

    def __init__(self, cfg: PetanqueCfg):
        if Pytanque is None:
            raise RuntimeError(
                "pytanque is not installed. `uv pip install "
                "git+https://github.com/llm4rocq/pytanque.git`"
            )
        self.cfg = cfg
        self._client: object | None = None

    def __enter__(self) -> "PetanqueDriver":
        self._client = Pytanque(self.cfg.host, self.cfg.port)
        self._client.__enter__()  # type: ignore[union-attr]
        return self

    def __exit__(self, *exc) -> None:
        if self._client is not None:
            self._client.__exit__(*exc)  # type: ignore[union-attr]
            self._client = None

    # --- proof lifecycle -------------------------------------------------

    def start(self, file: str, theorem: str) -> StepResult:
        """Open `file` and position at the start of `theorem`'s proof."""
        state = self._client.start(file, theorem)  # type: ignore[union-attr]
        return self._result(state, ok=True, error=None)

    def run(self, state: object, tactic: str) -> StepResult:
        """Apply one tactic (or `;`-chained block). Never raises on a Rocq error: the error
        is captured so the agent can repair from it."""
        try:
            new_state = self._client.run(state, tactic)  # type: ignore[union-attr]
            return self._result(new_state, ok=True, error=None)
        except Exception as e:  # petanque surfaces Rocq errors as exceptions
            return StepResult(ok=False, finished=False, goals=[], error=str(e), state=state)

    # --- helpers ---------------------------------------------------------

    def _result(self, state: object, *, ok: bool, error: str | None) -> StepResult:
        finished = bool(getattr(state, "proof_finished", False))
        goals = [] if finished else self._goals(state)
        return StepResult(ok=ok, finished=finished, goals=goals, error=error, state=state)

    def _goals(self, state: object) -> list[Goal]:
        raw = self._client.goals(state)  # type: ignore[union-attr]
        out: list[Goal] = []
        for g in raw or []:
            pretty = getattr(g, "pp", None) or str(g)
            hyps = [str(h) for h in getattr(g, "hyps", [])]
            concl = getattr(g, "ty", None) or pretty
            out.append(Goal(pretty=str(pretty), hypotheses=hyps, conclusion=str(concl)))
        return out


@contextmanager
def driver(cfg: PetanqueCfg) -> Iterator[PetanqueDriver]:
    with PetanqueDriver(cfg) as d:
        yield d
