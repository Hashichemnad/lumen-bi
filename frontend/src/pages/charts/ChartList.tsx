import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, FolderInput, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import MoveModal from "../../components/MoveModal";
import { Alert, Button, Card, Modal, PageHeader, Select, Table, formatDate } from "../../components/ui";
import type { SavedChart } from "../../lib/chart/types";
import { del, get, post } from "../../lib/api";
import { t } from "../../lib/i18n";
import { useWorkspace } from "../../lib/workspace";

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
    <Modal open title={t("New chart")} onClose={onClose}
      footer={<>
        <Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button>
        <Button disabled={!id} onClick={() => navigate(`/charts/new?dataset=${id}`)}>{t("Start")}</Button>
      </>}>
      {datasets.data && !datasets.data.length ? (
        <Alert tone="blue">{t("There are no datasets you can use yet. Ask a creator to share one with you.")}</Alert>
      ) : (
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-slate-700">{t("Dataset")}</span>
          <Select value={id} onChange={(e) => setId(e.target.value)} autoFocus>
            <option value="">{t("Choose a dataset")}</option>
            {datasets.data?.map((d) => <option key={d.id} value={d.id}>{t("{name} ({v1} rows)", { name: d.name, v1: d.row_count.toLocaleString() })}</option>)}
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
  const [params] = useSearchParams();
  const [creating, setCreating] = useState(params.get("new") === "1");
  const [moving, setMoving] = useState<SavedChart | null>(null);
  const { shows, current } = useWorkspace();
  const shown = (charts.data ?? []).filter((c) => shows(c.workspace_id));
  const [error, setError] = useState("");
  const remove = async (c: SavedChart) => {
    if (!confirm(t("Delete the chart \"{name}\"?", { name: c.name }))) return;
    try {
      await del(`/charts/${c.id}`);
      await qc.invalidateQueries({ queryKey: ["charts"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not delete"));
    }
  };
  const copy = async (c: SavedChart) => {
    const made = await post<SavedChart>(`/charts/${c.id}/copy`);
    await qc.invalidateQueries({ queryKey: ["charts"] });
    navigate(`/charts/${made.id}`);
  };
  return (
    <>
      <PageHeader title={current ? `${current.name} · ${t("Charts")}` : t("Charts")} description={t("Build charts from your datasets, then put them on dashboards.")}
        actions={<Button onClick={() => setCreating(true)}><Plus size={16} /> {t("New chart")}</Button>} />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <Card>
        {charts.data && !shown.length ? (
          <p className="p-8 text-center text-sm text-slate-500">{t("No charts yet. Start with")} <button className="text-brand-600 hover:underline" onClick={() => setCreating(true)}>{t("New chart")}</button>.</p>
        ) : (
          <Table head={[t(t("Name")), t(t("Dataset")), t(t("Workspace")), t(t("Owner")), t(t("Changed")), ""]}>
            {shown.map((c) => (
              <tr key={c.id}>
                <td className="px-4 py-2.5"><Link to={`/charts/${c.id}`} className="font-medium text-brand-700 hover:underline">{c.name}</Link></td>
                <td className="px-4 py-2.5 text-slate-600">{c.dataset_name}</td>
                <td className="px-4 py-2.5 text-slate-600">{c.workspace_name || t("Personal")}</td>
                <td className="px-4 py-2.5 text-slate-600">{c.owner_name}</td>
                <td className="whitespace-nowrap px-4 py-2.5 text-slate-500">{formatDate(c.updated_at)}</td>
                <td className="px-4 py-2.5 text-end">
                  <button onClick={() => copy(c)} className="rounded p-1.5 text-slate-500 hover:bg-slate-100" title={t("Copy")} aria-label={t("Copy")}><Copy size={15} /></button>
                  {c.level === "manage" && <button onClick={() => setMoving(c)} className="rounded p-1.5 text-slate-500 hover:bg-slate-100" title={t("Move to a workspace")} aria-label={t("Move to a workspace")}><FolderInput size={15} /></button>}
                  {c.level === "manage" && <button onClick={() => remove(c)} className="rounded p-1.5 text-slate-500 hover:bg-red-50 hover:text-red-600" title={t("Delete")} aria-label={t("Delete")}><Trash2 size={15} /></button>}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      {creating && <NewChartModal onClose={() => setCreating(false)} />}
      {moving && <MoveModal kind="charts" id={moving.id} current={moving.workspace_id} onClose={() => setMoving(null)} onMoved={() => qc.invalidateQueries({ queryKey: ["charts"] })} />}
    </>
  );
}
