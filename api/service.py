"""Job manager for the cloq-agent API: each upload runs the prove-c pipeline in a worker thread.

The proof engine (pet-server + the scaffold workspace) is a single shared resource, so jobs run on
a small thread pool (default one worker) off the request thread — POST /jobs returns immediately
with a job id while the engine churns. Stage transitions are appended to the job as the pipeline
emits them (via the report's on_stage hook), so GET /jobs/{id}/stream can replay them live.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from cloq_agent.config import Config
from cloq_agent.lift.compile import sanitize_ident
from cloq_agent.pipeline import run_prove_c, run_prove_machine_code
from cloq_agent.report import ProveCReport, StageRecord


# Upload extensions routed through the C-compile front door (gcc -> object) instead of the
# disassemble-an-object front door. Anything else is treated as a prebuilt RISC-V ELF/object.
C_SOURCE_SUFFIXES = (".c", ".i")


@dataclass
class Job:
    id: str
    mcu: str
    func: str | None
    prop: str
    secret: str | None
    mc_path: Path
    filename: str
    is_c: bool = False                 # True -> compile the upload with riscv gcc first
    status: str = "queued"             # queued | running | done | error
    events: list[dict] = field(default_factory=list)   # stage-transition records, in order
    report: ProveCReport | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        return {
            "id": self.id, "mcu": self.mcu, "func": self.func, "property": self.prop,
            "filename": self.filename, "is_c": self.is_c,
            "status": self.status, "created_at": self.created_at,
            "stages": list(self.events),
            "report": self.report.to_dict() if self.report else None,
            "error": self.error,
        }


class JobManager:
    def __init__(self, cfg: Config, repo_root: Path, work_dir: Path, max_workers: int = 1):
        self.cfg = cfg
        self.repo_root = Path(repo_root)
        # Created lazily on first submit (per-job dirs use parents=True), so merely constructing a
        # manager — e.g. the module-level default app at import — never writes to disk.
        self.work_dir = Path(work_dir)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        # One worker by default: the pet-server + scaffold workspace are shared, so jobs serialise.
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cloq-job")

    def submit(self, mc_bytes: bytes, filename: str, mcu: str, func: str | None,
               prop: str, secret: str | None) -> Job:
        jid = uuid.uuid4().hex[:12]
        jdir = self.work_dir / jid
        jdir.mkdir(parents=True, exist_ok=True)
        # Keep the uploaded basename so artifacts read naturally; default if empty.
        name = Path(filename or "program.o").name or "program.o"
        mc_path = jdir / name
        mc_path.write_bytes(mc_bytes)
        is_c = name.lower().endswith(C_SOURCE_SUFFIXES)
        job = Job(id=jid, mcu=mcu, func=func, prop=prop, secret=secret, mc_path=mc_path,
                  filename=name, is_c=is_c)
        with self._lock:
            self._jobs[jid] = job
        self._pool.submit(self._run, job)
        return job

    def _run(self, job: Job) -> None:
        job.status = "running"

        def on_stage(rec: StageRecord) -> None:
            job.events.append({"name": rec.name, "status": rec.status.value, "detail": rec.detail})

        try:
            if job.is_c:
                # A C upload is compiled with the pinned riscv gcc first (same front door as the
                # CLI `prove-c`). `func` names the symbol to prove; default to the file stem, which
                # is the convention for a self-contained unit (e.g. sum3.c defines `sum3`).
                func = job.func or sanitize_ident(Path(job.filename).stem)
                job.func = func
                job.report = run_prove_c(
                    c_path=job.mc_path, func=func, cfg=self.cfg, repo_root=self.repo_root,
                    prop=job.prop, secret=job.secret, on_stage=on_stage,
                )
            else:
                job.report = run_prove_machine_code(
                    mc_path=job.mc_path, func=job.func, cfg=self.cfg, repo_root=self.repo_root,
                    mcu=job.mcu, prop=job.prop, secret=job.secret, on_stage=on_stage,
                )
            job.status = "done"
        except Exception as e:  # a worker crash must not wedge the job — surface it
            job.error = f"{type(e).__name__}: {e}"
            job.status = "error"
        finally:
            job.finished.set()

    def get(self, jid: str) -> Job | None:
        with self._lock:
            return self._jobs.get(jid)

    def list_corpus(self) -> list[dict]:
        """Stored solved proofs from the RAG corpus (rag_store/records.jsonl), newest first-ish."""
        recs_path = Path(self.cfg.rag.store_dir) / "records.jsonl"
        if not recs_path.exists():
            return []
        out: list[dict] = []
        for line in recs_path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("kind") != "proof":
                continue
            out.append({
                "id": r.get("id"),
                "meta": r.get("meta", {}),
                "snippet": (r.get("text", "") or "")[:240],
            })
        return out

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
