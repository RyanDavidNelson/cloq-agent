import type { Stage, StageStatus } from "../types";

// The pipeline, in order. The first stage depends on the upload: a C source compiles
// (compile -> lift -> ...), a prebuilt binary disassembles (disassemble -> lift -> ...). The rest
// is shared: "classify/spec-lint/invariant/repair" is the prove group; "stored" is the write-back.
const TAIL = ["lift", "classify", "spec-lint", "invariant", "repair", "stored"];

const LABEL: Record<string, string> = {
  compile: "Compile",
  disassemble: "Disassemble",
  lift: "Lift",
  classify: "Classify",
  "spec-lint": "Spec-lint",
  invariant: "Invariant",
  repair: "Repair / search",
  stored: "Store to corpus",
};

const STATUS_LABEL: Record<StageStatus, string> = {
  ok: "ok",
  failed: "failed",
  skipped: "skipped",
  limitation: "expected fail",
};

export function StageProgress({
  stages,
  running,
  done,
}: {
  stages: Stage[];
  running: boolean;
  done: boolean;
}) {
  // Last status per stage name wins (e.g. lift may appear twice).
  const byName = new Map<string, Stage>();
  for (const s of stages) byName.set(s.name, s);
  // First stage is the intake: compile (C source) or disassemble (prebuilt binary). Default to
  // disassemble until the first event arrives.
  const intake = byName.has("compile") ? "compile" : "disassemble";
  const CANON = [intake, ...TAIL];
  const firstUnseen = CANON.find((n) => !byName.has(n));

  return (
    <div className="card">
      <h2 className="card-title">Pipeline</h2>
      <ol className="stepper">
        {CANON.map((name) => {
          const s = byName.get(name);
          const isRunning = running && !s && name === firstUnseen;
          const state = s ? s.status : isRunning ? "running" : "pending";
          return (
            <li key={name} className={`step step-${state}`}>
              <span className="step-marker" aria-hidden="true" />
              <div className="step-body">
                <span className="step-name">{LABEL[name] ?? name}</span>
                {s && <span className={`chip chip-${s.status}`}>{STATUS_LABEL[s.status]}</span>}
                {isRunning && <span className="chip chip-running">running…</span>}
              </div>
            </li>
          );
        })}
      </ol>

      {stages.length > 0 && (
        <div className="log">
          <div className="log-title">Event log</div>
          {stages.map((s, i) => (
            <div key={i} className={`log-row log-${s.status}`}>
              <span className={`chip chip-${s.status}`}>{STATUS_LABEL[s.status]}</span>
              <span className="log-name">{s.name}</span>
              {s.detail && <span className="log-detail">{s.detail}</span>}
            </div>
          ))}
          {running && <div className="log-row log-running"><span className="chip chip-running">running…</span></div>}
        </div>
      )}

      {!running && !done && stages.length === 0 && (
        <p className="muted">Upload a C source or a RISC-V binary to start the compile/disassemble → lift → prove pipeline.</p>
      )}
    </div>
  );
}
