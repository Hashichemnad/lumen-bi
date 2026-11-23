import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Alert, Button, Card, Modal, PageHeader, Select, Table, formatDate } from "../../components/ui";
import type { SavedChart } from "../../lib/chart/types";
import { del, get, post } from "../../lib/api";

interface CatalogItem {
  id: number;
  name: string;
  description: string;
  row_count: number;
  restricted: boolean;
}

function NewChartModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const datasets = useQuery({ queryKey: ["catalog"], queryFn: () => get<CatalogItem[]>("/catalog/datasets") });
  const [id, setId] = useState("");
  return (
    <Modal open title="New chart" onClose={onClose}
      footer={<>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button disabled={!id} onClick={() => navigate(`/charts/new?dataset=${id}`)}>Start</Button>
      </>}>
      {datasets.data && !datasets.data.length ? (
        <Alert tone="blue">There are no datasets you can use yet. Ask a creator to share one with you.</Alert>
      ) : (
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-slate-700">Dataset</span>
          <Select value={id} onChange={(e) => setId(e.target.value)} autoFocus>
            <option value="">Choose a dataset</option>
            {datasets.data?.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.row_count.toLocaleString()} rows)</option>)}
          </Select>
        </label>
      )}
    </Modal>
  );
}

export default function ChartList() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const charts = useQuery({ queryKey: ["charts"], queryFn: () => get<SavedChart[]>("/charts") });
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const remove = async (c: SavedChart) => {
    if (!confirm(`Delete the chart "${c.name}"?`)) return;
    try {
      await del(`/charts/${c.id}`);
      await qc.invalidateQueries({ queryKey: ["charts"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete");
    }
  };
  const copy = async (c: SavedChart) => {
    const made = await post<SavedChart>(`/charts/${c.id}/copy`);
    await qc.invalidateQueries({ queryKey: ["charts"] });
    navigate(`/charts/${made.id}`);
  };
  return (
    <>
      <PageHeader title="Charts" description="Build charts from your datasets. Dashboards combine them (next milestone)."
        actions={<Button onClick={() => setCreating(true)}><Plus size={16} /> New chart</Button>} />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <Card>
        {charts.data && !charts.data.length ? (
          <p className="p-8 text-center text-sm text-slate-500">No charts yet. Start with <button className="text-brand-600 hover:underline" onClick={() => setCreating(true)}>New chart</button>.</p>
        ) : (
          <Table head={["Name", "Dataset", "Owner", "Changed", ""]}>
            {charts.data?.map((c) => (
              <tr key={c.id}>
                <td className="px-4 py-2.5"><Link to={`/charts/${c.id}`} className="font-medium text-brand-700 hover:underline">{c.name}</Link></td>
                <td className="px-4 py-2.5 text-slate-600">{c.dataset_name}</td>
                <td className="px-4 py-2.5 text-slate-600">{c.owner_name}</td>
                <td className="whitespace-nowrap px-4 py-2.5 text-slate-500">{formatDate(c.updated_at)}</td>
                <td className="px-4 py-2.5 text-right">
                  <button onClick={() => copy(c)} className="rounded p-1.5 text-slate-500 hover:bg-slate-100" title="Copy" aria-label="Copy"><Copy size={15} /></button>
                  {c.can_edit && <button onClick={() => remove(c)} className="rounded p-1.5 text-slate-500 hover:bg-red-50 hover:text-red-600" title="Delete" aria-label="Delete"><Trash2 size={15} /></button>}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      {creating && <NewChartModal onClose={() => setCreating(false)} />}
    </>
  );
}
