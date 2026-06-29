"""FastAPI surface for cloq-agent: upload a C file, run prove-c in a worker, stream the stages.

Endpoints:
  GET  /health             active model backend + profile (never the API key)
  POST /jobs               multipart C upload + target options -> {job_id} (202, runs in a worker)
  GET  /jobs/{id}          status + the structured report
  GET  /jobs/{id}/stream   Server-Sent Events: one message per stage transition, then a final event
  GET  /corpus             solved proofs stored in the RAG corpus

The report a job produces is the exact same `ProveCReport` the CLI builds (both call
`cloq_agent.pipeline.run_prove_c`), so the API and CLI never drift.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from cloq_agent.config import Config, load_config, resolve_out_dir

from .service import Job, JobManager

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _model_backend(cfg: Config) -> str:
    """Classify the active model endpoint without revealing anything secret."""
    url = cfg.model.base_url or ""
    local_markers = ("localhost", "127.0.0.1", "host.docker.internal", "11434", "0.0.0.0")
    return "local" if any(m in url for m in local_markers) else "remote"


def create_app(cfg: Config | None = None, *, repo_root: Path | None = None,
               work_dir: Path | None = None) -> FastAPI:
    cfg = cfg or load_config()
    repo_root = Path(repo_root) if repo_root else _REPO_ROOT
    work_dir = Path(work_dir) if work_dir else (resolve_out_dir(cfg.eval.out_dir, repo_root) / "api_jobs")
    jm = JobManager(cfg, repo_root, work_dir)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        jm.shutdown()

    app = FastAPI(title="cloq-agent", version="0.1.0",
                  description="Agentic synthesis + machine-checking of Cloq timing proofs.",
                  lifespan=lifespan)
    app.state.cfg = cfg
    app.state.jm = jm

    @app.get("/health")
    def health() -> dict:
        """Liveness + the active model backend and config profile. The API key is never returned."""
        backend = _model_backend(cfg)
        # Profile comes from CLOQ_PROFILE if set, else inferred from the model endpoint.
        profile = os.environ.get("CLOQ_PROFILE") or ("local" if backend == "local" else "api")
        return {
            "status": "ok",
            "profile": profile,
            "model": {
                "name": cfg.model.name,
                "base_url": cfg.model.base_url,      # endpoint only; no credentials
                "backend": backend,
                "escalation_enabled": cfg.model.escalation.enabled,
            },
            "petanque": {"host": cfg.petanque.host, "port": cfg.petanque.port},
        }

    @app.post("/jobs", status_code=202)
    async def create_job(
        file: UploadFile = File(..., description="a RISC-V machine-code artifact (ELF / object)"),
        mcu: str = Form("neorv32", description="target microcontroller (only neorv32 today)"),
        func: str | None = Form(None, description="optional symbol to name the program"),
        property: str = Form("wcet", description="wcet | ct"),
        secret: str | None = Form(None, description="secret parameter (for property=ct)"),
    ) -> JSONResponse:
        if mcu != "neorv32":
            raise HTTPException(422, f"unsupported MCU '{mcu}' (only 'neorv32' is wired today)")
        if property not in ("wcet", "ct"):
            raise HTTPException(422, "property must be 'wcet' or 'ct'")
        data = await file.read()
        if not data:
            raise HTTPException(422, "empty upload")
        job = jm.submit(data, file.filename or "program.o", mcu, func, property, secret)
        return JSONResponse(status_code=202, content={"job_id": job.id, "status": job.status})

    @app.get("/jobs/{jid}")
    def get_job(jid: str) -> dict:
        job = jm.get(jid)
        if job is None:
            raise HTTPException(404, f"no such job: {jid}")
        return job.public()

    @app.get("/jobs/{jid}/stream")
    async def stream_job(jid: str) -> StreamingResponse:
        job = jm.get(jid)
        if job is None:
            raise HTTPException(404, f"no such job: {jid}")
        return StreamingResponse(_sse(job), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/corpus")
    def corpus() -> dict:
        items = jm.list_corpus()
        return {"count": len(items), "proofs": items}

    return app


async def _sse(job: Job):
    """Server-Sent Events: replay every stage transition as it appears, then one `final` event
    carrying the full report. Polls the job's event list (filled by the worker thread) and yields
    on the event loop so a slow proof never blocks the server."""
    sent = 0
    while True:
        while sent < len(job.events):
            yield f"data: {json.dumps(job.events[sent])}\n\n"
            sent += 1
        if job.finished.is_set():
            # flush any events appended between the check and the loop above
            while sent < len(job.events):
                yield f"data: {json.dumps(job.events[sent])}\n\n"
                sent += 1
            final = {
                "status": job.status,
                "error": job.error,
                "report": job.report.to_dict() if job.report else None,
            }
            yield f"event: final\ndata: {json.dumps(final)}\n\n"
            return
        await asyncio.sleep(0.05)


# Module-level app for `uvicorn api.main:app`.
app = create_app()
