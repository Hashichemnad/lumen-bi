import { useQuery } from "@tanstack/react-query";

import { get } from "./api";
import { language } from "./i18n";
import type { ChartConfig } from "./chart/types";
import type { QueryResult } from "./types";

export type AiFeature = "ask" | "explain" | "formula" | "dashboard" | "digest";

export interface AiStatus {
  available: boolean;
  features: Record<AiFeature, boolean>;
  local: boolean;
}

/** Which AI features have a model set up (hidden from people who cannot use them). */
export function useAi(enabled = true) {
  const q = useQuery({ queryKey: ["ai-status"], queryFn: () => get<AiStatus>("/ai/status"), staleTime: 5 * 60_000, enabled });
  return (feature: AiFeature) => !!q.data?.available && !!q.data.features[feature];
}

export interface AskAnswer {
  dataset_id: number;
  dataset_name: string;
  title: string;
  note: string;
  chart: string;
  config: ChartConfig;
  query: Record<string, unknown>;
  result: QueryResult;
  answer: string;
  provider: string;
}

export interface FormulaAnswer {
  name: string;
  formula: string;
  explanation: string;
  type: string;
  is_aggregate: boolean;
  sample: unknown[];
  provider: string;
}

export interface AiProvider {
  id: number;
  name: string;
  kind: "anthropic" | "openai" | "openai_compatible";
  base_url: string;
  model: string;
  has_key: boolean;
  local: boolean;
  enabled: boolean;
  is_default: boolean;
  features: AiFeature[];
  updated_at: string;
}

export interface ConnectedApp {
  user_id: number;
  client_id: string;
  name: string;
  connected_at: string;
  last_used_at: string | null;
  user?: string;
  email?: string;
}

/** Hand a chart over to the builder (it opens /charts/new?dataset=..&draft=1 and reads this once). */
const DRAFT_KEY = "lumen.chart-draft";

export function saveDraft(draft: { dataset_id: number; name: string; config: ChartConfig }) {
  try {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    /* private mode: the builder opens empty */
  }
}

export function takeDraft(datasetId: number): { name: string; config: ChartConfig } | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY);
    sessionStorage.removeItem(DRAFT_KEY);
    const d = raw ? JSON.parse(raw) : null;
    return d && d.dataset_id === datasetId ? d : null;
  } catch {
    return null;
  }
}

/** The language AI answers in: the page's language. */
export function aiLanguage(): "en" | "ar" {
  return language();
}
