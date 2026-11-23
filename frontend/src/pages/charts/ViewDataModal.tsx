import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";

import { Alert, Button, Modal } from "../../components/ui";
import { formatValue } from "../../lib/chart/format";
import { download, post } from "../../lib/api";
import type { QueryResult } from "../../lib/types";

/** The rows behind a chart or a mark (row-level security applies on the server). */
export default function ViewDataModal({ datasetId, spec, title, onClose, canExport, endpoint, exportEndpoint }: {
  datasetId: number; spec: Record<string, unknown>; title: string; onClose: () => void; canExport: boolean;
  /** Dashboards query through their own endpoints (the body then has no dataset) */
  endpoint?: string; exportEndpoint?: string;
}) {
  const body = endpoint ? spec : { dataset: datasetId, ...spec };
  const [error, setError] = useState("");
  const rows = useQuery({
    queryKey: ["view-data", endpoint ?? datasetId, JSON.stringify(spec)],
    queryFn: () => post<QueryResult>(endpoint ?? "/query", body),
  });
  const exportCsv = async (format: "csv" | "xlsx" = "csv") => {
    setError("");
    try {
      await download(exportEndpoint ?? "/query/export", { ...body, limit: undefined, format }, `data.${format}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    }
  };
  const r = rows.data;
  return (
    <Modal open wide title={title} onClose={onClose}
      footer={<>
        {canExport && <Button variant="secondary" onClick={() => exportCsv("csv")}><Download size={16} /> CSV</Button>}
        {canExport && <Button variant="secondary" onClick={() => exportCsv("xlsx")}><Download size={16} /> Excel</Button>}
        <Button onClick={onClose}>Close</Button>
      </>}>
      {(error || rows.error) && <Alert>{error || (rows.error as Error).message}</Alert>}
      {r && (
        <>
          <div className="max-h-[60vh] overflow-auto rounded-lg border border-slate-200">
            <table className="min-w-full text-sm">
              <thead className="sticky top-0 bg-slate-50">
                <tr>{r.columns.map((c) => <th key={c.key} className="whitespace-nowrap px-3 py-2 text-left text-xs font-semibold text-slate-600">{c.label}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {r.rows.map((row, i) => (
                  <tr key={i}>{row.map((v, j) => <td key={j} className="whitespace-nowrap px-3 py-1.5 tabular-nums">{v === null ? "" : formatValue(v, r.columns[j])}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-slate-500">
            {r.row_count.toLocaleString()} rows{r.truncated ? " shown (more exist; export to get all)" : ""}{r.restricted ? " · only rows you may see" : ""}
          </p>
        </>
      )}
    </Modal>
  );
}
