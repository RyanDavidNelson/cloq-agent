import { forwardRef } from "react";
import type { CorpusItem } from "../types";

export const CorpusPanel = forwardRef<HTMLDivElement, {
  items: CorpusItem[];
  highlight: string | null;
  onRefresh: () => void;
}>(function CorpusPanel({ items, highlight, onRefresh }, ref) {
  return (
    <div className="card" ref={ref}>
      <div className="card-head-row">
        <h2 className="card-title">Stored corpus</h2>
        <button className="btn btn-link" onClick={onRefresh}>
          Refresh
        </button>
      </div>
      <p className="muted small">
        Solved invariants + proofs written back for retrieval-augmented synthesis. {items.length} stored.
      </p>
      {items.length === 0 ? (
        <p className="muted">No proofs stored yet — a successful run is added here automatically.</p>
      ) : (
        <ul className="corpus-list">
          {items.map((it) => {
            const target = String((it.meta as Record<string, unknown>).target ?? "");
            const hit = highlight && target === highlight;
            return (
              <li key={it.id} className={`corpus-item ${hit ? "corpus-hit" : ""}`}>
                <div className="corpus-item-head">
                  <span className="mono corpus-id">{it.id}</span>
                  {target && <span className="badge">{target}</span>}
                  {hit && <span className="badge badge-ok">this run</span>}
                </div>
                <pre className="code code-snippet">{it.snippet}</pre>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
});
