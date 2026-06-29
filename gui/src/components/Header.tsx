import { AUTOCLOQ_ASCII } from "../ascii";
import type { Health } from "../types";

export function Header({ health }: { health: Health | null }) {
  return (
    <header className="hero">
      <pre className="ascii" aria-label="AutoCloq">
        {AUTOCLOQ_ASCII}
      </pre>
      <p className="subhead">
        AI-Generated, Formally-Verified, Tight Timing Constraints for Machine Code
      </p>
      <div className="hero-health" role="status" aria-live="polite">
        {health ? (
          <>
            <span className={`dot ${health.status === "ok" ? "dot-ok" : "dot-fail"}`} />
            <span className="hero-health-text">
              model <strong>{health.model.name}</strong>
              <span className="tag">{health.model.backend}</span>
              <span className="sep">·</span>profile {health.profile}
            </span>
          </>
        ) : (
          <span className="hero-health-text muted">connecting to backend…</span>
        )}
      </div>
    </header>
  );
}
