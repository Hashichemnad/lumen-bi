import { useQuery } from "@tanstack/react-query";
import { Cloud, Combine, Database, FileSpreadsheet, Globe, Map as MapIcon, Server } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Button, Card, PageHeader, Table, formatDate } from "../../components/ui";
import { get } from "../../lib/api";
import type { Connection, Dataset } from "../../lib/types";
import { CONNECTION_ICONS, CONNECTION_LABELS, StatusBadge, describeSchedule, formatNumber, sourceLabel } from "./common";
import { RestConnectionModal, SalesforceConnectionModal } from "./ApiConnectionForms";
import CombineModal from "./CombineModal";
import { DatabaseConnectionModal, UploadModal } from "./ConnectionForms";

export default function DataHome() {
  const navigate = useNavigate();
  const datasets = useQuery({
    queryKey: ["datasets"],
    queryFn: () => get<Dataset[]>("/datasets"),
    refetchInterval: (q) => (q.state.data?.some((d) => d.active_run) ? 2000 : false),
  });
  const connections = useQuery({ queryKey: ["connections"], queryFn: () => get<Connection[]>("/connections") });
  const [adding, setAdding] = useState<"file" | "oracle" | "postgres" | "salesforce" | "rest" | "combine" | null>(null);
  const goTo = (c: Connection) => navigate(`/data/connections/${c.id}`);

  return (
    <>
      <PageHeader title="Data" description="Connect your systems, choose what to copy, and keep it refreshed on a schedule." />

      <Card className="mb-8 p-5">
        <h2 className="font-semibold">Add data</h2>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setAdding("file")}><FileSpreadsheet size={16} /> Upload a file</Button>
          <Button variant="secondary" onClick={() => setAdding("oracle")}><Server size={16} /> Oracle</Button>
          <Button variant="secondary" onClick={() => setAdding("postgres")}><Database size={16} /> PostgreSQL</Button>
          <Button variant="secondary" onClick={() => setAdding("salesforce")}><Cloud size={16} /> Salesforce</Button>
          <Button variant="secondary" onClick={() => setAdding("rest")}><Globe size={16} /> REST API</Button>
          <Button variant="secondary" onClick={() => setAdding("combine")} disabled={!datasets.data?.some((d) => d.columns.length)}><Combine size={16} /> Join or combine datasets</Button>
          <Link to="/data/maps"><Button variant="secondary"><MapIcon size={16} /> Maps</Button></Link>
        </div>
      </Card>

      <h2 className="mb-3 text-lg font-semibold">Datasets</h2>
      <Card className="mb-8">
        <Table head={["Dataset", "Source", "Rows", "Status", "Refresh", "Last synced"]}>
          {datasets.data?.map((d) => (
            <tr key={d.id} className="cursor-pointer hover:bg-slate-50" onClick={() => navigate(`/data/datasets/${d.id}`)}>
              <td className="px-4 py-3 font-medium">{d.name}</td>
              <td className="px-4 py-3 text-slate-600">
                <div>{d.kind === "source" ? d.connection_name : d.kind === "join" ? "Join" : "Union"}</div>
                <div className="truncate text-xs text-slate-500">{sourceLabel(d)}</div>
              </td>
              <td className="px-4 py-3 tabular-nums">{d.current_version ? formatNumber(d.row_count) : "—"}</td>
              <td className="px-4 py-3"><StatusBadge dataset={d} /></td>
              <td className="px-4 py-3 text-slate-600">{describeSchedule(d.schedule)}</td>
              <td className="px-4 py-3 text-slate-500">{formatDate(d.last_synced_at)}</td>
            </tr>
          ))}
        </Table>
        {datasets.data?.length === 0 && (
          <p className="p-6 text-center text-sm text-slate-500">No datasets yet. Add a connection, then create a dataset from it.</p>
        )}
      </Card>

      <h2 className="mb-3 text-lg font-semibold">Connections</h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {connections.data?.map((c) => (
          <Link key={c.id} to={`/data/connections/${c.id}`}>
            <Card className="flex items-center gap-3 p-4 hover:bg-slate-50">
              <span className="text-slate-500">{CONNECTION_ICONS[c.type]}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{c.name}</span>
                <span className="block text-sm text-slate-500">
                  {CONNECTION_LABELS[c.type] ?? c.type} · {c.dataset_count} dataset{c.dataset_count === 1 ? "" : "s"}
                </span>
              </span>
            </Card>
          </Link>
        ))}
      </div>
      {connections.data?.length === 0 && <p className="text-sm text-slate-500">No connections yet.</p>}

      {adding === "file" && <UploadModal onClose={() => setAdding(null)} onDone={goTo} />}
      {adding === "combine" && <CombineModal onClose={() => setAdding(null)} onSaved={(d) => navigate(`/data/datasets/${d.id}`)} />}
      {adding === "salesforce" && <SalesforceConnectionModal onClose={() => setAdding(null)} onSaved={goTo} />}
      {adding === "rest" && <RestConnectionModal onClose={() => setAdding(null)} onSaved={goTo} />}
      {(adding === "oracle" || adding === "postgres") && (
        <DatabaseConnectionModal type={adding} onClose={() => setAdding(null)} onSaved={goTo} />
      )}
    </>
  );
}
