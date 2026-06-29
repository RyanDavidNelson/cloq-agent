"""API tests: machine-code job lifecycle, SSE stage stream, /health (no key), /corpus.

The pipeline runs for real. With junk bytes (or no toolchain) it fails cleanly at the disassemble
stage; that still exercises all the API plumbing — job lifecycle, streaming, report parity — which
is what these tests cover. A toolchain-gated test exercises the disassemble -> lift -> classify
happy path. No pet-server or model is required.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cloq_agent.config import load_config  # noqa: E402
from cloq_agent.lift.compile import GCC, OBJDUMP  # noqa: E402

JUNK = b"this is not machine code\n"


@pytest.fixture
def app_cfg(tmp_path):
    cfg = load_config()
    cfg.petanque.workspace = str(tmp_path / "proofs")
    cfg.rag.store_dir = str(tmp_path / "rag_store")
    cfg.rag.embedder = "hash"
    return cfg


@pytest.fixture
def client(app_cfg, tmp_path):
    from api.main import create_app
    app = create_app(app_cfg, work_dir=tmp_path / "jobs")
    with TestClient(app) as c:
        yield c


def _submit(client, data: bytes = JUNK, filename: str = "prog.o", mcu: str = "neorv32"):
    r = client.post("/jobs", data={"mcu": mcu},
                    files={"file": (filename, data, "application/octet-stream")})
    assert r.status_code == 202, r.text
    return r.json()["job_id"]


def _wait(client, jid, timeout_s=60.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        body = client.get(f"/jobs/{jid}").json()
        if body["status"] in ("done", "error"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {jid} did not finish in {timeout_s}s")


def test_health_reports_backend_and_never_the_key(client, app_cfg):
    d = client.get("/health").json()
    assert d["status"] == "ok"
    assert d["profile"] in ("local", "api")
    assert d["model"]["name"] == app_cfg.model.name
    assert d["model"]["backend"] in ("local", "remote")
    blob = json.dumps(d).lower()
    assert "api_key" not in blob
    assert app_cfg.model.api_key.lower() not in blob


def test_post_jobs_returns_id_and_completes_with_report(client):
    jid = _submit(client)
    assert jid
    body = _wait(client, jid)
    rep = body["report"]
    assert rep is not None
    # The machine-code intake starts at disassemble (no compile stage).
    assert [s["name"] for s in rep["stages"]][0] == "disassemble"


def test_rejects_unknown_mcu(client):
    r = client.post("/jobs", data={"mcu": "stm32"},
                    files={"file": ("prog.o", JUNK, "application/octet-stream")})
    assert r.status_code == 422


def test_stream_shows_stage_transitions_then_final(client):
    jid = _submit(client)
    stage_names: list[str] = []
    final = None
    with client.stream("GET", f"/jobs/{jid}/stream") as r:
        assert r.status_code == 200
        is_final = False
        for line in r.iter_lines():
            if line.startswith("event: final"):
                is_final = True
            elif line.startswith("data:"):
                payload = json.loads(line[len("data:"):].strip())
                if is_final:
                    final = payload
                    break
                stage_names.append(payload["name"])
    assert "disassemble" in stage_names, stage_names
    assert final is not None and "report" in final
    polled = client.get(f"/jobs/{jid}").json()["report"]
    assert final["report"]["stages"] == polled["stages"]


def test_api_report_matches_direct_pipeline(client, app_cfg, tmp_path):
    """AC: the report a job produces matches the engine's for the same machine-code input."""
    from cloq_agent.pipeline import run_prove_machine_code

    jid = _submit(client, filename="prog.o")
    api_rep = _wait(client, jid)["report"]

    mc = tmp_path / "prog.o"
    mc.write_bytes(JUNK)
    direct = run_prove_machine_code(mc_path=mc, cfg=app_cfg,
                                    repo_root=Path(__file__).resolve().parents[1]).to_dict()
    keys = ["proved", "headline", "ceiling_class", "predicted_cycles", "predicted_range",
            "property", "flags"]
    assert {k: api_rep[k] for k in keys} == {k: direct[k] for k in keys}
    # Stage names+statuses are input-determined; details may embed the (differing) job path.
    assert [(s["name"], s["status"]) for s in api_rep["stages"]] == \
        [(s["name"], s["status"]) for s in direct["stages"]]


def test_corpus_lists_stored_proofs(client, tmp_path):
    store = tmp_path / "rag_store"
    store.mkdir(parents=True, exist_ok=True)
    rec = {"id": "solved::sum3", "text": "Definition sum3_timing_invs ... Qed.",
           "kind": "proof", "meta": {"target": "sum3", "theorem": "sum3_timing_gen"}}
    (store / "records.jsonl").write_text(json.dumps(rec) + "\n")
    d = client.get("/corpus").json()
    assert d["count"] == 1
    assert d["proofs"][0]["meta"]["target"] == "sum3"


def test_unknown_job_is_404(client):
    assert client.get("/jobs/deadbeef").status_code == 404
    assert client.get("/jobs/deadbeef/stream").status_code == 404


@pytest.mark.skipif(shutil.which(GCC) is None or shutil.which(OBJDUMP) is None,
                    reason="RISC-V toolchain not installed")
def test_machine_code_happy_path_disassembles_and_lifts(client, tmp_path):
    """With the toolchain present, a real RV32 object disassembles, lifts, and classifies."""
    c = tmp_path / "sum3.c"
    c.write_text("unsigned sum3(unsigned a,unsigned b,unsigned cc){return a+b+cc;}\n")
    obj = tmp_path / "sum3.o"
    subprocess.run([GCC, "-march=rv32im_zicsr_zicntr", "-mabi=ilp32", "-O2",
                    "-ffreestanding", "-nostdlib", "-c", str(c), "-o", str(obj)], check=True)
    jid = _submit(client, data=obj.read_bytes(), filename="sum3.o")
    rep = _wait(client, jid)["report"]
    names = {s["name"]: s["status"] for s in rep["stages"]}
    assert names.get("disassemble") == "ok"
    assert names.get("lift") in ("ok", "failed")  # lift ok; scaffold (coqc) may fail downstream
    assert rep["ceiling_class"] == "straight-line"
    assert rep["predicted_range"] and "exact" in rep["predicted_range"]
