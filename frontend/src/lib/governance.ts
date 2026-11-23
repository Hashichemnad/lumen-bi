/** Types for milestone 12: KPI status, comments, versions, subscriptions, alerts, display links, checks. */

export type KpiStatus = "on_track" | "at_risk" | "off_track" | "no_data";

export interface KpiResult {
  section: string;
  code: string;
  title: string;
  target: number;
  direction: "higher" | "lower";
  warn: number;
  tile: string;
  actual: number | null;
  format: Record<string, unknown> | null;
  status: KpiStatus;
  error: string | null;
}

export interface CommentItem {
  id: number;
  tile_id: string | null;
  parent_id: number | null;
  body: string;
  anchor: { label: string } | null;
  resolved: boolean;
  author: string;
  author_id: number | null;
  created_at: string;
  edited_at: string | null;
  can_edit: boolean;
  can_delete: boolean;
}

export interface VersionItem {
  id: number;
  name: string;
  saved_at: string;
  saved_by: string;
  tiles: number | null;
}

export interface HistoryItem {
  at: string;
  channel: string;
  recipient: string;
  status: "sent" | "failed" | "skipped";
  error: string;
}

export interface Recipients {
  users: number[];
  groups: number[];
}

export interface SubscriptionItem {
  id: number;
  name: string;
  recipients: Recipients;
  channels: string[];
  format: "pdf" | "png" | "link";
  schedule: string;
  state: string | null;
  message: string;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: string;
  last_error: string;
  owner: string;
  can_edit: boolean;
  history: HistoryItem[];
}

export interface AlertCondition {
  kind: "value" | "kpi" | "anomaly";
  op?: "gt" | "gte" | "lt" | "lte";
  value?: number;
  status?: "off_track" | "at_risk";
  /** anomaly: how unusual before it tells you, and which way */
  sensitivity?: "low" | "medium" | "high";
  direction?: "both" | "up" | "down";
}

export interface AlertItem {
  id: number;
  name: string;
  tile_id: string;
  condition: AlertCondition;
  description: string;
  recipients: Recipients;
  channels: string[];
  schedule: string;
  enabled: boolean;
  triggered: boolean;
  last_value: string;
  last_error: string;
  last_checked_at: string | null;
  last_triggered_at: string | null;
  owner: string;
  can_edit: boolean;
  history: HistoryItem[];
}

export interface DisplayLinkItem {
  id: number;
  name: string;
  run_as: { id: number; name: string };
  created_at: string;
  expires_at: string | null;
  revoked: boolean;
  last_used_at: string | null;
  url?: string;
}

export interface DataCheckItem {
  id: number;
  kind: "freshness" | "row_count" | "not_null" | "unique" | "range" | "allowed";
  config: Record<string, unknown>;
  severity: "warning" | "error";
  enabled: boolean;
  description: string;
  last_status: "never" | "pass" | "fail" | "error";
  last_value: string;
  last_message: string;
  last_run_at: string | null;
}

export const STATUS_LABEL: Record<KpiStatus, string> = {
  on_track: "On track",
  at_risk: "At risk",
  off_track: "Off track",
  no_data: "No data",
};
