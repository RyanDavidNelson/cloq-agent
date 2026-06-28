"""Unit tests for the per-tactic timeout in PetanqueDriver.run (Task 2).

No server needed: we swap in a fake client whose `run` either sleeps past the
budget (timeout), raises (Rocq error), or returns quickly (passthrough).
"""
from __future__ import annotations

import time

import pytest

from cloq_agent.config import PetanqueCfg
from cloq_agent.proof import petanque_driver as pd
from cloq_agent.proof.petanque_driver import PetanqueDriver

pytestmark = pytest.mark.skipif(
    pd.Pytanque is None, reason="pytanque not installed in this environment"
)

PARENT = object()  # sentinel parent state handle


class _FakeState:
    proof_finished = False


class _SleepyClient:
    """Sleeps `delay`s regardless of the native `timeout` kwarg — i.e. a server that
    ignores its own timeout, so the client-side guard must do the work."""

    def __init__(self, delay: float):
        self.delay = delay
        self.calls = 0
        self.last_timeout = "unset"

    def run(self, state, tactic, timeout=None):
        self.calls += 1
        self.last_timeout = timeout
        time.sleep(self.delay)
        return _FakeState()

    def goals(self, state):
        return []


class _RaisingClient:
    def run(self, state, tactic, timeout=None):
        raise RuntimeError("Rocq error: tactic failed")

    def goals(self, state):
        return []


def _driver(client, default_timeout_s) -> PetanqueDriver:
    d = PetanqueDriver(PetanqueCfg(), default_timeout_s=default_timeout_s)
    d._client = client
    return d


def test_timeout_yields_failure_and_parent_state():
    d = _driver(_SleepyClient(delay=5.0), default_timeout_s=0.1)
    t0 = time.time()
    res = d.run(PARENT, "repeat step.")
    elapsed = time.time() - t0

    assert res.ok is False
    assert res.finished is False
    assert res.goals == []
    assert res.state is PARENT          # parent handed back, exactly like a Rocq error
    assert "timeout after 0.1s" in (res.error or "")
    # Returned well before the 5s sleep (budget 0.1 + 2s grace), so the search isn't stalled.
    assert elapsed < 4.0


def test_per_call_timeout_overrides_default():
    d = _driver(_SleepyClient(delay=5.0), default_timeout_s=999.0)
    res = d.run(PARENT, "psimpl.", timeout_s=0.1)
    assert res.ok is False
    assert res.state is PARENT
    assert "timeout after 0.1s" in (res.error or "")


def test_rocq_error_still_captured_with_parent_state():
    d = _driver(_RaisingClient(), default_timeout_s=0.1)
    res = d.run(PARENT, "lia.")
    assert res.ok is False
    assert res.state is PARENT
    assert "Rocq error" in (res.error or "")
    assert "timeout" not in (res.error or "")


def test_fast_tactic_passes_through():
    client = _SleepyClient(delay=0.0)
    d = _driver(client, default_timeout_s=5.0)
    res = d.run(PARENT, "now step.")
    assert res.ok is True
    assert res.finished is False
    assert isinstance(res.state, _FakeState)


def test_native_timeout_passed_as_int_seconds():
    """pytanque forwards the native timeout to coq-lsp as an INTEGER number of seconds
    ('This number is not an integer.' otherwise); a float budget must be coerced."""
    client = _SleepyClient(delay=0.0)
    d = _driver(client, default_timeout_s=20.0)
    d.run(PARENT, "idtac.")
    assert isinstance(client.last_timeout, int)
    assert client.last_timeout == 20


def test_zero_budget_disables_timeout():
    client = _SleepyClient(delay=0.0)
    d = _driver(client, default_timeout_s=0.0)
    res = d.run(PARENT, "idtac.")
    assert res.ok is True
    assert client.calls == 1
