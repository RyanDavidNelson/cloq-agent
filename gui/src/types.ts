export type StageStatus = "ok" | "failed" | "skipped" | "limitation";

export interface Stage {
  name: string;
  status: StageStatus;
  detail: string;
}

export interface Report {
  target: string;
  func: string;
  property: string;
  proved: boolean;
  headline: string;
  ceiling_class: string | null;
  predicted_cycles: string | null;
  predicted_range: string | null;
  toolchain_version: string | null;
  flags: string[];
  attempts: number;
  iterations: number;
  llm_calls: number;
  added_to_corpus: boolean;
  compile_log: string;
  lift_log: string;
  residual_goal: string | null;
  error: string | null;
  stages: Stage[];
}

export interface Health {
  status: string;
  profile: string;
  model: {
    name: string;
    base_url: string;
    backend: string;
    escalation_enabled: boolean;
  };
  petanque: { host: string; port: number };
}

export interface CorpusItem {
  id: string;
  meta: Record<string, unknown>;
  snippet: string;
}

export interface FinalEvent {
  status: string;
  error: string | null;
  report: Report | null;
}
