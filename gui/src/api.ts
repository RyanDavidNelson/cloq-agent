import type { CorpusItem, FinalEvent, Health, Report, Stage } from "./types";

// Same-origin: nginx (prod) / vite (dev) proxy /api -> the FastAPI backend.
const BASE = "/api";

export async function getHealth(): Promise<Health> {
  const r = await fetch(`${BASE}/health`);
  if (!r.ok) throw new Error(`health ${r.status}`);
  return r.json();
}

export async function submitJob(
  file: File,
  mcu: string,
  func?: string,
): Promise<{ job_id: string; status: string }> {
  const fd = new FormData();
  fd.append("file", file, file.name);
  fd.append("mcu", mcu);
  if (func && func.trim()) fd.append("func", func.trim());
  const r = await fetch(`${BASE}/jobs`, { method: "POST", body: fd });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const j = await r.json();
      detail = j.detail ?? detail;
    } catch {
      /* keep status */
    }
    throw new Error(detail);
  }
  return r.json();
}

export async function getJob(id: string): Promise<{ status: string; report: Report | null; error: string | null; stages: Stage[] }> {
  const r = await fetch(`${BASE}/jobs/${id}`);
  if (!r.ok) throw new Error(`job ${r.status}`);
  return r.json();
}

export async function getCorpus(): Promise<{ count: number; proofs: CorpusItem[] }> {
  const r = await fetch(`${BASE}/corpus`);
  if (!r.ok) throw new Error(`corpus ${r.status}`);
  return r.json();
}

export interface StreamHandlers {
  onStage: (s: Stage) => void;
  onFinal: (f: FinalEvent) => void;
  onError: (msg: string) => void;
}

// Subscribe to a job's SSE stage stream. Returns an unsubscribe function.
export function streamJob(id: string, h: StreamHandlers): () => void {
  const es = new EventSource(`${BASE}/jobs/${id}/stream`);
  let finished = false;

  es.onmessage = (ev) => {
    try {
      h.onStage(JSON.parse(ev.data) as Stage);
    } catch {
      /* ignore malformed frame */
    }
  };
  es.addEventListener("final", (ev) => {
    finished = true;
    try {
      h.onFinal(JSON.parse((ev as MessageEvent).data) as FinalEvent);
    } catch {
      h.onError("could not parse final report");
    }
    es.close();
  });
  es.onerror = () => {
    if (finished) return; // normal close after the final event
    h.onError("lost connection to the job stream");
    es.close();
  };
  return () => es.close();
}
