"""The ceiling gate: a ceiling-classified target fails fast with the structured diagnostic and
does NOT run the prover by default; --force-synthesis opts in under a clamped budget. Server-free
(monkeypatches the lift + spec build, so no pet-server/coqc needed)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloq_agent import pipeline
from cloq_agent.config import AgentCfg, load_config
from cloq_agent.lift.intake import Ceiling, LiftResult
from cloq_agent.report import ProveCReport, Status


def _fake_lift(*, invariant):
    """A LiftResult as the ceiling/in-scope branch would produce. `invariant=None` => ceiling
    (no deterministic CFG invariant); a string => in-scope (straight-line/counter loop)."""
    return LiftResult(
        ok=True,
        func="f",
        ceiling=Ceiling.ARRAY_POINTER if invariant is None else Ceiling.COUNTER_LOOP,
        cfg_description="loop over an array (test fixture)",
        scaffold_source="(* scaffold *)",
        scaffold_module="Fixture",
        entry_addr=0x10,
        exit_addrs=[0x20],
        postcondition="cycle_count_of_trace t' = 5",
        invariant=invariant,
    )


def _run(monkeypatch, *, invariant, force_synthesis, on_build_targetspec=None):
    """Drive _prove_from_compiled with lift stubbed. build_targetspec is the first thing past the
    gate, so we trip a sentinel there to detect whether the gate let the run through."""
    monkeypatch.setattr(pipeline.intake, "lift", lambda *a, **k: _fake_lift(invariant=invariant))
    if on_build_targetspec is not None:
        monkeypatch.setattr(pipeline.intake, "build_targetspec", on_build_targetspec)
    rep = ProveCReport(target="t.c", func="f", prop="wcet")
    return pipeline._prove_from_compiled(
        rep, object(), cfg=load_config(), repo_root=Path("."), prop="wcet",
        secret=None, force_synthesis=force_synthesis,
    )


def test_ceiling_default_fails_fast_without_running_prover(monkeypatch):
    def _boom(*a, **k):  # build_targetspec must NOT be reached on the default path
        raise AssertionError("prover path entered for a ceiling class without --force-synthesis")

    rep = _run(monkeypatch, invariant=None, force_synthesis=False, on_build_targetspec=_boom)

    assert rep.proved is False
    assert rep.expected_failure  # labelled as a known limitation, not a crash
    inv = next(s for s in rep.stages if s.name == "invariant")
    assert inv.status is Status.LIMITATION and "skipped" in inv.detail.lower()
    classify = next(s for s in rep.stages if s.name == "classify")
    assert "not attempted" in classify.detail
    assert "force-synthesis" in (rep.error or "")


def test_force_synthesis_lets_ceiling_through_the_gate(monkeypatch):
    sentinel = RuntimeError("reached build_targetspec")

    def _boom(*a, **k):
        raise sentinel

    with pytest.raises(RuntimeError, match="reached build_targetspec"):
        _run(monkeypatch, invariant=None, force_synthesis=True, on_build_targetspec=_boom)


def test_in_scope_target_always_attempts_regardless_of_flag(monkeypatch):
    sentinel = RuntimeError("reached build_targetspec")

    def _boom(*a, **k):
        raise sentinel

    # A deterministic invariant (counter loop) is never gated, even without --force-synthesis.
    with pytest.raises(RuntimeError, match="reached build_targetspec"):
        _run(monkeypatch, invariant="Definition f_timing_invs := ...",
             force_synthesis=False, on_build_targetspec=_boom)


def test_ceiling_clamp_config_defaults_and_env_override(monkeypatch):
    a = AgentCfg()
    assert a.ceiling_invariant_attempts == 1
    assert a.ceiling_search_max_runs == 40
    assert a.ceiling_invariant_attempts < a.invariant_attempts  # the clamp is a real reduction
    assert a.ceiling_search_max_runs < a.search_max_runs
    monkeypatch.setenv("CLOQ_AGENT_CEILING_SEARCH_MAX_RUNS", "7")
    assert load_config().agent.ceiling_search_max_runs == 7
