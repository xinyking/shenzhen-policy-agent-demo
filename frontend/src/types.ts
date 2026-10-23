export type Source = {
  source_id: string;
  title: string;
  source_url: string;
  published_at: string | null;
  last_checked_at: string;
  last_check_status: string;
  version: string;
  evidence_id?: string;
  text?: string;
  locator?: string;
  paragraphs?: number;
  domains?: string[];
};

export type Result = {
  follow_up?: { question: string; fields: string[] } | null;
  mode_requested?: string;
  question: string;
  status: string;
  mode_used: string;
  answer: string;
  claims: { title?: string; kind?: "summary" | "interpretation" | "suggestion"; text: string; evidence_ids: string[] }[];
  sources: Source[];
  missing_information: string[];
  warnings: string[];
  checked_at: string;
  report_markdown: string;
  agent_trace?: unknown;
  verification?: { status: string };
  failure?: { stage: string; code: string; message: string };
  routing?: { method: string; domains: { id: string; name: string }[]; unknown: boolean };
  schedule_drafts?: ScheduleDraft[];
  multi_agent_trace?: { agent: string; status: string; count?: number }[];
  memory_used?: boolean;
  execution_profile?: string;
  screenshot_analysis?: ScreenshotAnalysis;
};

export type ScreenshotAnalysis = {
  status: string; title?: string; is_application_page?: boolean; message?: string;
  materials: { id: string; name: string; requirement: "required" | "conditional" | "optional" | "unclear"; condition: string; quote: string; location: string }[];
  procedure?: string; dates: { date: string; label: string; quote: string; location: string }[]; gaps: string[];
  checks?: Record<string, { verdict: string; explanation: string; policy_quote: string; policy_evidence_ids: string[] }>;
};

export type MaterialDraft = { text: string; evidence_id: string; quote: string; source_kind?: "policy" | "screenshot";
  location?: string; source_title?: string; requirement?: "required" | "conditional" | "optional" | "unclear"; condition?: string };

export type RunEvent = {
  run_id: string; seq: number; at: string; type: string; node: string | null;
  data: { elapsed_ms?: number; source_count?: number; status?: string; reason?: string };
};

export type RunStatus = {
  run_id: string; status: string; node: string | null; result_available: boolean; error?: string;
};

export type ScheduleDraft = {
  title: string;
  materials: MaterialDraft[];
  method: string;
  notes?: string;
  date: string | null;
  requires_confirmation: boolean;
  sources: { evidence_id: string; title: string; source_url: string; version: string; locator: string }[];
};

export type ModelStatus = {
  base_url: string;
  model_name: string;
  vision_model_name?: string;
  configured: boolean;
  key_present: boolean;
  origin: string;
  persistence?: string;
  load_error?: boolean;
};
