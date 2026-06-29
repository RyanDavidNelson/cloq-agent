"""Unit tests for the prove-c structured report."""
from __future__ import annotations

import json

from cloq_agent.report import ProveCReport, Status, neorv32_cycle_range


def test_stage_records_and_failed_stage():
    rep = ProveCReport(target="f.c", func="f")
    rep.stage("compile", Status.OK).stage("lift", Status.OK)
    rep.stage("classify", Status.LIMITATION, "array/pointer loop")
    assert rep.failed_stage is not None
    assert rep.failed_stage.name == "classify"
    assert rep.failed_stage.status is Status.LIMITATION


def test_render_names_stage_and_class():
    rep = ProveCReport(target="asum.c", func="asum")
    rep.ceiling_class = "array/pointer loop"
    rep.stage("compile", Status.OK, "-> asum.o")
    rep.stage("classify", Status.LIMITATION, "needs an exists-index invariant")
    rep.error = "proof stalled at: exhausted invariant attempts"
    out = rep.render()
    assert "asum" in out and "array/pointer loop" in out
    assert "classify" in out and "needs an exists-index invariant" in out
    # A ceiling diagnostic is not a crash: no traceback noise, and it reads as NOT PROVED.
    assert "NOT PROVED" in out


def test_render_is_ascii_only_no_emoji():
    rep = ProveCReport(target="f.c", func="f", proved=True)
    rep.stage("compile", Status.OK).stage("invariant", Status.OK).stage("stored", Status.OK)
    out = rep.render()
    assert out.isascii(), "prove-c output must be emoji/glyph-free"


def test_expected_failure_is_labelled_with_diagnosis():
    rep = ProveCReport(target="asum.c", func="asum")
    rep.ceiling_class = "array/pointer loop"
    rep.stage("compile", Status.OK)
    rep.stage("classify", Status.LIMITATION, "attempting anyway")
    rep.stage("invariant", Status.LIMITATION, "stalled at: exhausted invariant attempts")
    rep.error = "expected failure for array/pointer loop; proof stalled at: exhausted invariant attempts"
    out = rep.render()
    assert rep.expected_failure is True
    assert "expected failure for this ceiling class" in out
    assert "[xfail ]" in out
    assert "diagnosis:" in out and "stalled at" in out


def test_to_json_roundtrips_stages():
    rep = ProveCReport(target="f.c", func="f", proved=True)
    rep.predicted_cycles = "tadd + tadd"
    rep.stage("compile", Status.OK).stage("invariant", Status.OK)
    d = json.loads(rep.to_json())
    assert d["proved"] is True
    assert d["predicted_cycles"] == "tadd + tadd"
    assert [s["name"] for s in d["stages"]] == ["compile", "invariant"]
    assert d["stages"][0]["status"] == "ok"


def test_neorv32_range_exact_for_fixed_cost_straightline():
    # sum3: two adds (2 each), no memory/branch -> exact 4 cycles.
    assert neorv32_cycle_range("cycle_count_of_trace t' = tadd + tadd") == \
        "4 cycles (exact for NEORV32BaseConfig)"


def test_neorv32_range_carries_abstract_memory_latency():
    # a load + an add: 4 (load base) + 2 = 6, plus one data-memory wait-state parameter.
    out = neorv32_cycle_range("tlw + tadd")
    assert out.startswith("6 + T_data_latency cycles")
    assert ">= 6" in out


def test_neorv32_range_none_for_loop_form_or_unknown():
    assert neorv32_cycle_range("tfbeq + x * (taddi)") is None     # parametric loop form
    assert neorv32_cycle_range("tbogus + tadd") is None           # unknown token


def test_neorv32_range_handles_shift_offset():
    # tslli 2 = 3 + offset(2) = 5, + tadd(2) = 7, all concrete.
    assert neorv32_cycle_range("tslli 2 + tadd") == "7 cycles (exact for NEORV32BaseConfig)"


def test_markdown_success_has_closed_form_and_corpus():
    rep = ProveCReport(target="sum3.c", func="sum3", proved=True)
    rep.predicted_cycles = "tadd + tadd"
    rep.predicted_range = "4 cycles (exact for NEORV32BaseConfig)"
    rep.added_to_corpus = True
    rep.stage("compile", Status.OK).stage("invariant", Status.OK).stage("stored", Status.OK)
    md = rep.to_markdown()
    assert "PROVED" in md and "## Stages" in md
    assert "`tadd + tadd`" in md and "4 cycles" in md
    assert "added to corpus" in md and "yes" in md
    assert md.isascii()


def test_html_failure_shows_residual_goal_and_class():
    rep = ProveCReport(target="asum.c", func="asum")
    rep.ceiling_class = "array/pointer loop"
    rep.residual_goal = "cycle_count_of_trace t' = 4 + s R_A5"
    rep.stage("classify", Status.LIMITATION, "attempting anyway")
    rep.stage("repair", Status.LIMITATION, "proof search stalled")
    rep.error = "expected failure for array/pointer loop"
    h = rep.to_html()
    assert "<table" in h and "array/pointer loop" in h
    assert "Last residual goal" in h and "R_A5" in h
    assert "expected failure for this ceiling class" in h
    # HTML-escaped, no raw angle brackets leaking from the goal text.
    assert "<script" not in h.lower()


def test_to_json_includes_phase3_fields():
    rep = ProveCReport(target="f.c", func="f", proved=True)
    rep.predicted_range = "4 cycles (exact for NEORV32BaseConfig)"
    rep.added_to_corpus = True
    rep.attempts, rep.iterations, rep.llm_calls = 1, 0, 0
    d = json.loads(rep.to_json())
    assert d["predicted_range"].startswith("4 cycles")
    assert d["added_to_corpus"] is True
    assert d["attempts"] == 1 and "lift_log" in d and "residual_goal" in d
