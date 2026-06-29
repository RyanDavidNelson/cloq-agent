import { useState } from "react";
import type { Report } from "../types";

function failingStage(r: Report) {
  const bad = r.stages.filter((s) => s.status === "failed" || s.status === "limitation");
  return bad.length ? bad[bad.length - 1] : null;
}

export function ResultPanel({ report, onViewCorpus }: { report: Report; onViewCorpus: () => void }) {
  if (report.proved) return <ProofView report={report} onViewCorpus={onViewCorpus} />;
  return <DiagnosticView report={report} onViewCorpus={onViewCorpus} />;
}

function ProofView({ report, onViewCorpus }: { report: Report; onViewCorpus: () => void }) {
  return (
    <div className="card result result-ok">
      <div className="result-head">
        <span className="result-badge badge-ok">Proved</span>
        <h2 className="card-title">
          <span className="mono">{report.func}</span> — {report.property.toUpperCase()} verified
        </h2>
      </div>

      <div className="kv">
        <span className="kv-key">Cycle-count closed form</span>
        <pre className="code code-strong">{report.predicted_cycles ?? "—"}</pre>
      </div>
      <div className="kv">
        <span className="kv-key">Predicted range (NEORV32)</span>
        <div className="kv-val mono">{report.predicted_range ?? "—"}</div>
      </div>

      <div className="meta-row">
        <Meta label="class" value={report.ceiling_class ?? "—"} />
        <Meta label="attempts" value={String(report.attempts)} />
        <Meta label="LLM calls" value={String(report.llm_calls)} />
      </div>

      <div className="corpus-note">
        {report.added_to_corpus ? (
          <>
            <span className="badge badge-ok">added to corpus</span>
            <button className="btn btn-link" onClick={onViewCorpus}>
              View the stored proof →
            </button>
          </>
        ) : (
          <span className="muted">not written to the corpus</span>
        )}
      </div>

      <Provenance report={report} />
    </div>
  );
}

function DiagnosticView({ report, onViewCorpus }: { report: Report; onViewCorpus: () => void }) {
  const expected = report.headline.toLowerCase().includes("expected failure");
  const fs = failingStage(report);
  return (
    <div className={`card result ${expected ? "result-xfail" : "result-fail"}`}>
      <div className="result-head">
        <span className={`result-badge ${expected ? "badge-xfail" : "badge-fail"}`}>
          {expected ? "Expected limitation" : "Not proved"}
        </span>
        <h2 className="card-title">
          <span className="mono">{report.func}</span> — diagnostic
        </h2>
      </div>

      <p className="result-headline">{report.headline}</p>

      <div className="meta-row">
        {report.ceiling_class && <Meta label="ceiling class" value={report.ceiling_class} highlight />}
        {fs && <Meta label="failing stage" value={fs.name} />}
        <Meta label="attempts" value={String(report.attempts)} />
      </div>

      {fs?.detail && (
        <div className="kv">
          <span className="kv-key">What happened at <span className="mono">{fs.name}</span></span>
          <div className="kv-val">{fs.detail}</div>
        </div>
      )}

      {report.residual_goal && (
        <div className="kv">
          <span className="kv-key">Last residual goal</span>
          <pre className="code">{report.residual_goal}</pre>
        </div>
      )}

      {report.error && (
        <div className="kv">
          <span className="kv-key">Diagnosis</span>
          <div className="kv-val">{report.error}</div>
        </div>
      )}

      <Logs report={report} />

      <div className="corpus-note">
        <button className="btn btn-link" onClick={onViewCorpus}>
          Browse the stored corpus →
        </button>
      </div>

      <Provenance report={report} />
    </div>
  );
}

function Meta({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className={`meta ${highlight ? "meta-hi" : ""}`}>
      <span className="meta-label">{label}</span>
      <span className="meta-value">{value}</span>
    </div>
  );
}

function Logs({ report }: { report: Report }) {
  const [open, setOpen] = useState(false);
  const has = report.compile_log.trim() || report.lift_log.trim();
  if (!has) return null;
  return (
    <div className="logs">
      <button className="btn btn-link" onClick={() => setOpen((v) => !v)}>
        {open ? "Hide" : "Show"} compile / lift logs
      </button>
      {open && (
        <>
          {report.lift_log.trim() && (
            <>
              <div className="kv-key">CFG (lift)</div>
              <pre className="code">{report.lift_log}</pre>
            </>
          )}
          {report.compile_log.trim() && (
            <>
              <div className="kv-key">compiler stderr</div>
              <pre className="code">{report.compile_log}</pre>
            </>
          )}
        </>
      )}
    </div>
  );
}

function Provenance({ report }: { report: Report }) {
  return (
    <div className="provenance">
      {report.toolchain_version && (
        <span title={report.toolchain_version}>toolchain: {report.toolchain_version.split("\n")[0]}</span>
      )}
      {report.flags.length > 0 && <span className="mono">flags: {report.flags.join(" ")}</span>}
    </div>
  );
}
