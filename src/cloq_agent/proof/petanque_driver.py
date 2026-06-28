"""Proof-engine control via petanque (the proof API that ships inside coq-lsp).

We do NOT talk to Rocq directly and we do NOT spawn coqtop. petanque exposes a low-latency,
gym-like interface (start / run-tactic / read-goals) over TCP, and `pytanque` is its Python
client. This module is a thin, typed wrapper that the orchestrator drives.

Run the server first (inside the rocq container):

    pet-server --address 0.0.0.0 --port 8765

petanque state semantics (Task 1, probed empirically against the pinned pytanque):
HANDLES STAY LIVE — an OLD state handle remains runnable after NEWER states are
produced from the same parent in the same session (`run(s0,..)->s1`, then
`run(s0,..)->s1b`, then `run(s1,..)` still succeeds). So the DFS search MAY hold
many live state handles. `replay_from_root` is kept as a defensive fallback in
case a handle is ever rejected as stale.
"""
from __future__ import annotations

import threading
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

    # Extra wall-clock slack on top of the per-tactic budget before the client-side guard
    # fires. The native (server-side) timeout should abort first and let the socket recover;
    # this guard only catches a server that ignores its own timeout, so it can stay generous.
    _TIMEOUT_GRACE_S = 2.0

    def __init__(self, cfg: PetanqueCfg, default_timeout_s: float | None = None):
        if Pytanque is None:
            raise RuntimeError(
                "pytanque is not installed. `uv pip install "
                "git+https://github.com/llm4rocq/pytanque.git`"
            )
        self.cfg = cfg
        # Per-tactic wall budget (agent.tactic_timeout_s). None disables the timeout.
        self._default_timeout_s = default_timeout_s
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
        """Open `file` and position at the start of `theorem`'s proof. Never raises on a Rocq
        error: a malformed generated file (e.g. a model-proposed invariant that doesn't type-
        check, so the theorem can't be elaborated) is captured as ok=False so the orchestrator
        records a *failed* proof and retries, rather than crashing the run."""
        try:
            state = self._client.start(file, theorem)  # type: ignore[union-attr]
            return self._result(state, ok=True, error=None)
        except Exception as e:  # petanque surfaces Rocq/load errors as exceptions
            return StepResult(ok=False, finished=False, goals=[], error=str(e), state=None)

    def run(self, state: object, tactic: str, timeout_s: float | None = None) -> StepResult:
        """Apply one tactic (or `;`-chained block). Never raises on a Rocq error OR a timeout:
        both are captured so the agent can repair/backtrack from them.

        A hung `repeat step`/`psimpl` (see vendored uxQueueSpacesAvailable.v "psimpl hangs")
        would otherwise stall a search that fires many candidates per node. Bounded by
        `timeout_s` (defaulting to the driver's `agent.tactic_timeout_s`); on timeout we return
        the PARENT `state` with ok=False, exactly like a Rocq error, so the search backtracks."""
        budget = self._default_timeout_s if timeout_s is None else timeout_s
        try:
            new_state = self._run_tactic(state, tactic, budget)
            return self._result(new_state, ok=True, error=None)
        except TimeoutError:
            return StepResult(ok=False, finished=False, goals=[],
                              error=f"timeout after {budget}s", state=state)
        except Exception as e:  # petanque surfaces Rocq errors as exceptions
            return StepResult(ok=False, finished=False, goals=[], error=str(e), state=state)

    def _run_tactic(self, state: object, tactic: str, budget: float | None) -> object:
        """Run one tactic, optionally bounded by `budget` seconds. With a budget we pass the
        native pytanque timeout (server aborts the tactic, keeping the socket in sync) AND run
        in a daemon thread guarded by `budget + grace`, so a server that ignores its own
        timeout still can't stall the search. Raises TimeoutError when the budget is exceeded."""
        if not budget or budget <= 0:
            return self._client.run(state, tactic)  # type: ignore[union-attr]

        # pytanque's native timeout is forwarded to coq-lsp as an INTEGER number of seconds
        # ("This number is not an integer." if given a float), so coerce; the float `budget`
        # still drives the finer-grained thread guard below.
        native_timeout = max(1, int(round(budget)))

        box: dict[str, object] = {}

        def worker() -> None:
            try:
                box["value"] = self._client.run(  # type: ignore[union-attr]
                    state, tactic, timeout=native_timeout)
            except BaseException as e:  # propagate Rocq errors to the caller thread
                box["error"] = e

        th = threading.Thread(target=worker, daemon=True)
        th.start()
        th.join(budget + self._TIMEOUT_GRACE_S)
        if th.is_alive():
            # Server ignored its own timeout; abandon the (daemon) thread and backtrack.
            raise TimeoutError(f"tactic exceeded {budget}s")
        if "error" in box:
            raise box["error"]  # type: ignore[misc]
        return box["value"]

    def replay_from_root(self, file: str, theorem: str, path: list[str]) -> StepResult:
        """Defensive fallback for the proof search: re-`start` the proof and replay every
        tactic in `path`, returning the final StepResult. Used when a stored state handle is
        rejected as stale (probing showed handles stay live, so this is a safety net, not the
        hot path). On any failing step it short-circuits, returning that step's ok=False
        StepResult with the failing tactic surfaced in `error`."""
        start = self.start(file, theorem)
        if not start.ok:
            return start
        cur = start
        for tac in path:
            cur = self.run(cur.state, tac)
            if not cur.ok:
                return StepResult(ok=False, finished=False, goals=[],
                                  error=f"replay failed at {tac!r}: {cur.error}",
                                  state=cur.state)
        return cur

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
def driver(cfg: PetanqueCfg, default_timeout_s: float | None = None) -> Iterator[PetanqueDriver]:
    with PetanqueDriver(cfg, default_timeout_s=default_timeout_s) as d:
        yield d
