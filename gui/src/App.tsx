import { useEffect, useRef, useState } from "react";
import { getCorpus, getHealth, streamJob, submitJob } from "./api";
import { CorpusPanel } from "./components/CorpusPanel";
import { Header } from "./components/Header";
import { ResultPanel } from "./components/ResultPanel";
import { StageProgress } from "./components/StageProgress";
import { UploadForm, type SubmitArgs } from "./components/UploadForm";
import type { CorpusItem, Health, Report, Stage } from "./types";

type Phase = "idle" | "running" | "done" | "error";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [stages, setStages] = useState<Stage[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [corpus, setCorpus] = useState<CorpusItem[]>([]);
  const [lastTarget, setLastTarget] = useState<string | null>(null);
  const corpusRef = useRef<HTMLDivElement>(null);
  const unsubRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null));
    refreshCorpus();
    return () => unsubRef.current?.();
  }, []);

  function refreshCorpus() {
    getCorpus().then((c) => setCorpus(c.proofs)).catch(() => {});
  }

  async function onSubmit(a: SubmitArgs) {
    unsubRef.current?.();
    setPhase("running");
    setStages([]);
    setReport(null);
    setError(null);
    setLastTarget(a.file.name.replace(/\.[^.]+$/, ""));
    try {
      const { job_id } = await submitJob(a.file, a.mcu, a.func);
      unsubRef.current = streamJob(job_id, {
        onStage: (s) => setStages((prev) => [...prev, s]),
        onFinal: (f) => {
          setReport(f.report);
          setPhase(f.status === "error" ? "error" : "done");
          if (f.error && !f.report) setError(f.error);
          refreshCorpus();
        },
        onError: (msg) => {
          setError(msg);
          setPhase("error");
        },
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  }

  function viewCorpus() {
    refreshCorpus();
    corpusRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <div className="app">
      <Header health={health} />
      <main className="layout">
        <section className="col col-left">
          <UploadForm onSubmit={onSubmit} busy={phase === "running"} />
        </section>

        <section className="col col-right">
          <StageProgress stages={stages} running={phase === "running"} done={phase === "done"} />

          {error && !report && (
            <div className="card result result-fail">
              <span className="result-badge badge-fail">Error</span>
              <p className="result-headline">The job could not run to completion.</p>
              <div className="kv">
                <span className="kv-key">Details</span>
                <div className="kv-val">{error}</div>
              </div>
            </div>
          )}

          {report && <ResultPanel report={report} onViewCorpus={viewCorpus} />}

          <CorpusPanel ref={corpusRef} items={corpus} highlight={report?.proved ? (report?.func ?? lastTarget) : null} onRefresh={refreshCorpus} />
        </section>
      </main>
      <footer className="footer">
        <span>
          Generator–verifier: the model only proposes; Rocq checks. No proof enters the corpus
          without passing the prover.
        </span>
      </footer>
    </div>
  );
}
