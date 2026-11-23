import { useQuery } from "@tanstack/react-query";
import { Cloud, Database, FileSpreadsheet, Globe, Server } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "../../components/ui";
import { get } from "../../lib/api";
import type { Dataset, SyncRun } from "../../lib/types";
import { t } from "../../lib/i18n";

export const CONNECTION_ICONS: Record<string, ReactNode> = {
  file: <FileSpreadsheet size={20} />,
  oracle: <Server size={20} />,
  postgres: <Database size={20} />,
  salesforce: <Cloud size={20} />,
  rest: <Globe size={20} />,
};

export const CONNECTION_LABELS: Record<string, string> = {
  file: "Uploaded file",
  oracle: "Oracle",
  postgres: "PostgreSQL",
  salesforce: "Salesforce",
  rest: "REST API",
};

export function useDataSettings() {
  return useQuery({
    queryKey: ["data-settings"],
    queryFn: () => get<{ timezone: string; max_upload_mb: number }>("/data-settings"),
    staleTime: Infinity,
  });
}

export function StatusBadge({ dataset }: { dataset: Pick<Dataset, "last_status" | "active_run"> }) {
  if (dataset.active_run) return <Badge tone="blue">{dataset.active_run.status === "queued" ? t("Queued") : t("Syncing…")}</Badge>;
  if (dataset.last_status === "success") return <Badge tone="green">{t("Up to date")}</Badge>;
  if (dataset.last_status === "failed") return <Badge tone="red">{t("Last sync failed")}</Badge>;
  return <Badge>{t("Not synced")}</Badge>;
}

export function RunBadge({ run }: { run: SyncRun }) {
  const tone = { queued: "slate", running: "blue", success: "green", failed: "red" } as const;
  return <Badge tone={tone[run.status]}>{run.status}</Badge>;
}

export function formatNumber(n: number): string {
  return n.toLocaleString();
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function sourceLabel(d: Pick<Dataset, "source" | "kind" | "parents">): string {
  if (d.kind === "join") return t("Join of {names}", { names: d.parents.map((p) => p.name).join(" + ") });
  if (d.kind === "union") return t("Union of {names}", { names: d.parents.map((p) => p.name).join(", ") });
  return d.source.object ? d.source.object : t("Custom query");
}

// ---------- schedules: friendly presets <-> cron ----------
export type Preset = "manual" | "15m" | "30m" | "hourly" | "daily" | "weekdays" | "custom";

export function describeSchedule(cron: string | null): string {
  const p = parseCron(cron);
  switch (p.preset) {
    case "manual": return t("Manual only");
    case "15m": return t("Every 15 minutes");
    case "30m": return t("Every 30 minutes");
    case "hourly": return t("Every hour");
    case "daily": return t("Daily at {time}", { time: p.time });
    case "weekdays": return t("Weekdays at {time}", { time: p.time });
    default: return t("Custom: {cron}", { cron });
  }
}

export function parseCron(cron: string | null): { preset: Preset; time: string } {
  if (!cron) return { preset: "manual", time: "06:00" };
  if (cron === "*/15 * * * *") return { preset: "15m", time: "06:00" };
  if (cron === "*/30 * * * *") return { preset: "30m", time: "06:00" };
  if (cron === "0 * * * *") return { preset: "hourly", time: "06:00" };
  const m = cron.match(/^(\d{1,2}) (\d{1,2}) \* \* (\*|1-5)$/);
  if (m) {
    const time = `${m[2].padStart(2, "0")}:${m[1].padStart(2, "0")}`;
    return { preset: m[3] === "*" ? "daily" : "weekdays", time };
  }
  return { preset: "custom", time: "06:00" };
}

export function buildCron(preset: Preset, time: string, custom: string): string | null {
  const [h, mi] = time.split(":").map((x) => String(Number(x)));
  switch (preset) {
    case "manual": return null;
    case "15m": return "*/15 * * * *";
    case "30m": return "*/30 * * * *";
    case "hourly": return "0 * * * *";
    case "daily": return `${mi} ${h} * * *`;
    case "weekdays": return `${mi} ${h} * * 1-5`;
    default: return custom.trim() || null;
  }
}
