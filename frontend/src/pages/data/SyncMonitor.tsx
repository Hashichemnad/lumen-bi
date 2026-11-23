/** Data → Sync monitor: every dataset's syncs over the last 7 days, failures first. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, CheckCircle2, Clock, Loader2, RefreshCw } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Alert, Button, Card, PageHeader } from "../../components/ui";
import { get, post } from "../../lib/api";
import { formatMoment } from "../../lib/dates";
import { t } from "../../lib/i18n";

interface Row {
  id: number; name: string; kind: string; connection: string; connection_type: string; schedule: string | null;
  next_run_at: string | null; last_synced_at: string | null; last_status: string; row_count: number; running: boolean;
  runs_7d: number; failed_7d: number; avg_seconds: number | null; last_seconds: number | null; last_error: string; late: boolean;
}
interface Monitor { datasets: Row[]; failures: { dataset_id: number; at: string; error: string; trigger: string }[]; health: Record<string, number>; rows_last_24h: number }

const ORDER = (r: Row) => (r.last_status === "failed" ? 0 : r.late ? 1 : r.running ? 2 : r.last_status === "never" ? 3 : 4);

function State({ r }: { r: Row }) {
  if (r.running) return <span className="inline-flex items-center gap-1 text-xs font-semibold text-brand-700"><Loader2 size={13} className="animate-spin" /> {t("Syncing")}</span>;
  if (r.last_status === "failed") return <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-xs font-semibold text-red-700"><AlertTriangle size={12} /> {t("Failed")}</span>;
  if (r.late) return <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-800"><Clock size={12} /> {t("Late")}</span>;
  if (r.last_status === "never") return <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600">{t("Not synced yet")}</span>;
  return <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-semibold text-emerald-700"><CheckCircle2 size={12} /> {t("OK")}</span>;
}

export default function SyncMonitor() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["sync-monitor"], queryFn: () => get<Monitor>("/admin/sync-monitor"), refetchInterval: 10_000 });
  const [error, setError] = useState("");
  const retry = async (r: Row) => {
    setError("");
    try {
      await post(`/datasets/${r.id}/sync`);
      await qc.invalidateQueries({ queryKey: ["sync-monitor"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Could not start the sync"));
    }
  };
  const m = q.data;
  const rows = [...(m?.datasets ?? [])].sort((a, b) => ORDER(a) - ORDER(b) || a.name.localeCompare(b.name));
  const secs = (s: number | null) => (s === null ? "—" : s < 60 ? `${s.toFixed(0)} s` : `${(s / 60).toFixed(1)} min`);
  return (
    <>
      <PageHeader title={t("Sync monitor")} description={t("Every dataset's refreshes over the last 7 days. Refreshes every 10 seconds.")} />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      {m && (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-4">
            {[["ok", "Up to date", "text-emerald-700"], ["failed", "Failed", "text-red-700"], ["late", "Late", "text-amber-700"], ["never", "Not synced yet", "text-slate-600"]].map(([k, label, cls]) => (
              <Card key={k} className="p-4">
                <p className="text-xs font-medium text-slate-500">{t(label)}</p>
                <p className={clsx("mt-1 text-2xl font-bold tabular-nums", (m.health[k] ?? 0) > 0 ? cls : "text-slate-400")}>{m.health[k] ?? 0}</p>
              </Card>
            ))}
          </div>
          <Card className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500">
                  <th className="border-b border-slate-200 px-4 py-2 text-start font-medium">{t("Dataset")}</th>
                  <th className="border-b border-slate-200 px-3 py-2 text-start font-medium">{t("State")}</th>
                  <th className="hidden border-b border-slate-200 px-3 py-2 text-start font-medium md:table-cell">{t("Last synced")}</th>
                  <th className="hidden border-b border-slate-200 px-3 py-2 text-start font-medium lg:table-cell">{t("Next")}</th>
                  <th className="border-b border-slate-200 px-3 py-2 text-end font-medium">{t("Runs (failed)")}</th>
                  <th className="hidden border-b border-slate-200 px-3 py-2 text-end font-medium sm:table-cell">{t("Usual time")}</th>
                  <th className="border-b border-slate-200 px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="align-top hover:bg-slate-50">
                    <td className="border-b border-slate-100 px-4 py-2.5">
                      <Link to={`/data/datasets/${r.id}`} className="font-medium text-slate-900 hover:text-brand-700">{r.name}</Link>
                      <span className="block text-xs text-slate-500">{r.connection || t(r.kind === "join" ? "Joined" : r.kind === "union" ? "Combined" : "Dataset")} · {r.row_count.toLocaleString()} {t("rows")}</span>
                      {r.last_error && <span className="mt-1 block max-w-md text-xs text-red-700">{r.last_error}</span>}
                    </td>
                    <td className="border-b border-slate-100 px-3 py-2.5"><State r={r} /></td>
                    <td className="hidden border-b border-slate-100 px-3 py-2.5 text-slate-600 md:table-cell">{r.last_synced_at ? formatMoment(r.last_synced_at) : "—"}</td>
                    <td className="hidden border-b border-slate-100 px-3 py-2.5 text-slate-600 lg:table-cell">{r.schedule ? (r.next_run_at ? formatMoment(r.next_run_at) : "—") : t("Manual")}</td>
                    <td className="border-b border-slate-100 px-3 py-2.5 text-end tabular-nums">{r.runs_7d}{r.failed_7d ? <span className="text-red-700"> ({r.failed_7d})</span> : ""}</td>
                    <td className="hidden border-b border-slate-100 px-3 py-2.5 text-end tabular-nums sm:table-cell">{secs(r.avg_seconds)}</td>
                    <td className="border-b border-slate-100 px-4 py-2.5 text-end">
                      {r.kind === "source" || r.kind === "join" || r.kind === "union" ? (
                        <Button size="sm" variant="secondary" disabled={r.running} onClick={() => retry(r)}><RefreshCw size={13} /> {r.last_status === "failed" ? t("Retry") : t("Sync now")}</Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <p className="text-xs text-slate-500">{t("Rows read in the last 24 hours: {n}", { n: m.rows_last_24h.toLocaleString() })}</p>
        </div>
      )}
    </>
  );
}
