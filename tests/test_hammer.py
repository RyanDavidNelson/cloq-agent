"""The hammer ladder carries the Picinae workhorses and skips a timed-out rung (Task 4)."""
from __future__ import annotations

from cloq_agent.proof.hammer import LADDER, try_ladder
from cloq_agent.proof.petanque_driver import StepResult


def test_ladder_contains_workhorses_cheapest_first():
    # The real Picinae closers are present...
    for rung in ("now step.", "repeat step; hammer.",
                 "repeat step; psimpl; hammer.", "repeat step; psimpl; lia."):
        assert rung in LADDER
    # ...ahead of the generic leaf closers (cheapest/most-bounded first).
    assert LADDER.index("now step.") == 0
    assert LADDER.index("repeat step; hammer.") < LADDER.index("hammer.")
    assert LADDER.index("repeat step; psimpl; lia.") < LADDER.index("lia.")


def test_ladder_has_loop_arithmetic_rungs():
    # The modular-counter closers (msub_nowrap / N_sub_distr) the generic ladder used to miss.
    assert any("msub_nowrap" in r for r in LADDER)
    assert any("N_sub_distr" in r for r in LADDER)
    # They sit after the cheap workhorses but before the bare generic closers (more expensive).
    loop_idx = min(i for i, r in enumerate(LADDER) if "msub_nowrap" in r)
    assert LADDER.index("repeat step; hammer.") < loop_idx < LADDER.index("hammer.")
    # Both a focused-goal (`now`) and a whole-fanout (`all:`) form are present.
    assert any(r.startswith("now (") and "msub_nowrap" in r for r in LADDER)
    assert any(r.startswith("all: (") and "msub_nowrap" in r for r in LADDER)


def test_ladder_has_no_program_local_tactics():
    # The noverlap closers (`preserve_noverlaps`/`unfold_noverlap`) are program-specific Local Ltac
    # and out of scope in generated proofs, so they must NOT appear as ladder rungs (would no-op).
    blob = " ".join(LADDER)
    assert "preserve_noverlaps" not in blob
    assert "unfold_noverlap" not in blob


class _FakeDriver:
    """Records the tactics tried and the timeout passed; `timeout_on` rungs come back as a
    captured timeout (ok=False), `closer` is the rung that finishes the proof."""

    def __init__(self, *, timeout_on: set[str], closer: str):
        self.timeout_on = timeout_on
        self.closer = closer
        self.tried: list[str] = []
        self.timeouts: list[float | None] = []

    def run(self, state, tactic, timeout_s=None):
        self.tried.append(tactic)
        self.timeouts.append(timeout_s)
        if tactic in self.timeout_on:
            # exactly what PetanqueDriver.run returns on a timeout: parent state, ok=False
            return StepResult(ok=False, finished=False, goals=[],
                              error=f"timeout after {timeout_s}s", state=state)
        if tactic == self.closer:
            return StepResult(ok=True, finished=True, goals=[], error=None, state="DONE")
        return StepResult(ok=False, finished=False, goals=[], error=None, state=state)


def test_timed_out_rung_is_skipped():
    # The two `repeat step` workhorses hang; the ladder must skip them and close on a later rung.
    d = _FakeDriver(timeout_on={"repeat step; hammer.", "repeat step; psimpl; hammer."},
                    closer="hammer.")
    out = try_ladder(d, state="S0")
    assert out.closed is True
    assert out.tactic == "hammer."
    assert out.state == "DONE"
    # The hung rungs were attempted (and skipped), and "hammer." was reached after them.
    assert "repeat step; hammer." in d.tried
    assert d.tried.index("repeat step; hammer.") < d.tried.index("hammer.")


def test_timeout_is_forwarded_to_driver():
    d = _FakeDriver(timeout_on=set(), closer="now step.")
    try_ladder(d, state="S0", timeout_s=7.5)
    assert d.timeouts[0] == 7.5  # per-tactic budget threaded into driver.run


def test_no_rung_closes_returns_open_with_parent_state():
    d = _FakeDriver(timeout_on=set(LADDER), closer="<none>")
    out = try_ladder(d, state="S0")
    assert out.closed is False
    assert out.tactic is None
    assert out.state == "S0"
    assert d.tried == LADDER  # every rung attempted
