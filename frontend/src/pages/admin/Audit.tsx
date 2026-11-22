import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Card, PageHeader, Pager, Select, Table, formatDate } from "../../components/ui";
import { get } from "../../lib/api";
import type { AuditEntry, Page } from "../../lib/types";

const PAGE_SIZE = 50;
const ACTIONS = [
  { value: "", label: "All activity" },
  { value: "auth.", label: "Sign-ins and passwords" },
  { value: "auth.login_failed", label: "Failed sign-ins" },
  { value: "user.", label: "User changes" },
  { value: "group.", label: "Group changes" },
];

export default function Audit() {
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);
  const params = new URLSearchParams({ action, page: String(page), page_size: String(PAGE_SIZE) });
  const log = useQuery({ queryKey: ["audit", action, page], queryFn: () => get<Page<AuditEntry>>(`/admin/audit?${params}`) });

  return (
    <>
      <PageHeader title="Audit log" description="Sign-ins and changes, newest first." />
      <Card>
        <div className="border-b border-slate-200 p-4">
          <Select value={action} className="max-w-60" onChange={(e) => { setAction(e.target.value); setPage(1); }}>
            {ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
          </Select>
        </div>
        <Table head={["When", "Who", "Action", "Object", "Details", "IP"]}>
          {log.data?.items.map((e) => (
            <tr key={e.id}>
              <td className="whitespace-nowrap px-4 py-2.5 text-slate-500">{formatDate(e.created_at)}</td>
              <td className="px-4 py-2.5">{e.user_email || "—"}</td>
              <td className="px-4 py-2.5 font-mono text-xs">{e.action}</td>
              <td className="px-4 py-2.5 text-slate-500">{e.object_type ? `${e.object_type} #${e.object_id}` : "—"}</td>
              <td className="max-w-xs truncate px-4 py-2.5 font-mono text-xs text-slate-500" title={JSON.stringify(e.details)}>
                {Object.keys(e.details).length ? JSON.stringify(e.details) : ""}
              </td>
              <td className="px-4 py-2.5 text-slate-500">{e.ip}</td>
            </tr>
          ))}
        </Table>
        {log.data && <Pager page={page} pageSize={PAGE_SIZE} total={log.data.total} onPage={setPage} />}
      </Card>
    </>
  );
}
