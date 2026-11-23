import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, Plus, Trash2, UserPlus } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Alert, Badge, Button, Card, Field, Input, Modal, PageHeader, Select } from "../components/ui";
import { del, get, post, put } from "../lib/api";
import { useAuth } from "../lib/auth";
import { t } from "../lib/i18n";
import { useWorkspace, type Workspace, type WorkspaceRole } from "../lib/workspace";

interface Member {
  principal: "user" | "group";
  id: number;
  role: WorkspaceRole;
  name: string;
  email: string;
}

interface Principal {
  id: number;
  name: string;
  email?: string;
}

const ROLE_HELP: Record<WorkspaceRole, string> = {
  viewer: "Opens the dashboards and charts",
  editor: "Also changes them and adds new ones",
  manager: "Also deletes and shares them, and manages members",
};

function roleLabel(r: WorkspaceRole) {
  return t({ viewer: "Viewer", editor: "Editor", manager: "Manager" }[r]);
}

function MembersModal({ ws, onClose }: { ws: Workspace; onClose: () => void }) {
  const qc = useQueryClient();
  const canManage = ws.my_role === "manager";
  const current = useQuery({ queryKey: ["workspace-members", ws.id], queryFn: () => get<Member[]>(`/workspaces/${ws.id}/members`) });
  const directory = useQuery({
    queryKey: ["directory"],
    queryFn: () => get<{ users: Principal[]; groups: Principal[] }>("/directory"),
    enabled: canManage,
  });
  const [members, setMembers] = useState<Member[]>([]);
  const [name, setName] = useState(ws.name);
  const [description, setDescription] = useState(ws.description);
  const [error, setError] = useState("");
  useEffect(() => {
    if (current.data) setMembers(current.data);
  }, [current.data]);

  const add = (principal: "user" | "group", id: number) => {
    const list = principal === "user" ? directory.data?.users : directory.data?.groups;
    const p = list?.find((x) => x.id === id);
    if (!p || members.some((m) => m.principal === principal && m.id === id)) return;
    setMembers([...members, { principal, id, role: "viewer", name: p.name, email: p.email ?? "" }]);
  };
  const save = async () => {
    setError("");
    try {
      await put(`/workspaces/${ws.id}`, { name, description });
      await put(`/workspaces/${ws.id}/members`, members.map(({ principal, id, role }) => ({ principal, id, role })));
      await qc.invalidateQueries({ queryKey: ["workspaces"] });
      await qc.invalidateQueries({ queryKey: ["workspace-members", ws.id] });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? t(err.message) : t("Could not save"));
    }
  };

  return (
    <Modal open wide title={canManage ? t("Edit {name}", { name: ws.name }) : ws.name} onClose={onClose}
      footer={canManage ? <>
        <Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button>
        <Button onClick={save}>{t("Save")}</Button>
      </> : <Button variant="secondary" onClick={onClose}>{t("Close")}</Button>}>
      {error && <Alert>{error}</Alert>}
      {canManage && (
        <>
          <Field label={t("Name")}><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label={t("Description")}><Input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        </>
      )}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-medium text-slate-700">{t("Members")} ({members.length})</span>
        </div>
        <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
          {members.map((m, i) => (
            <li key={`${m.principal}-${m.id}`} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
              <span className="min-w-0 flex-1">
                <span className="font-medium">{m.name || m.email}</span>
                {m.principal === "group" ? <Badge tone="blue">{t("Group")}</Badge> : m.name && <span className="ms-2 text-slate-500">{m.email}</span>}
              </span>
              {canManage ? (
                <>
                  <Select aria-label={t("Role")} value={m.role} className="w-36 py-1.5"
                    onChange={(e) => setMembers(members.map((x, j) => (j === i ? { ...x, role: e.target.value as WorkspaceRole } : x)))}>
                    {(["viewer", "editor", "manager"] as WorkspaceRole[]).map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
                  </Select>
                  <button onClick={() => setMembers(members.filter((_, j) => j !== i))} aria-label={t("Remove")}
                    className="rounded-lg p-2 text-slate-500 hover:bg-red-50 hover:text-red-600"><Trash2 size={15} /></button>
                </>
              ) : <Badge>{roleLabel(m.role)}</Badge>}
            </li>
          ))}
          {!members.length && <li className="px-3 py-4 text-center text-sm text-slate-500">{t("No members yet.")}</li>}
        </ul>
      </div>
      {canManage && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("Add a person")}>
            <Select value="" onChange={(e) => add("user", Number(e.target.value))}>
              <option value="">{t("Choose…")}</option>
              {directory.data?.users.filter((u) => !members.some((m) => m.principal === "user" && m.id === u.id)).map((u) => (
                <option key={u.id} value={u.id}>{u.name ? `${u.name} (${u.email})` : u.email}</option>
              ))}
            </Select>
          </Field>
          <Field label={t("Add a group")}>
            <Select value="" onChange={(e) => add("group", Number(e.target.value))}>
              <option value="">{t("Choose…")}</option>
              {directory.data?.groups.filter((g) => !members.some((m) => m.principal === "group" && m.id === g.id)).map((g) => (
                <option key={g.id} value={g.id}>{g.name}</option>
              ))}
            </Select>
          </Field>
        </div>
      )}
      <ul className="space-y-1 rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
        {(["viewer", "editor", "manager"] as WorkspaceRole[]).map((r) => (
          <li key={r}><span className="font-semibold text-slate-700">{roleLabel(r)}:</span> {t(ROLE_HELP[r])}</li>
        ))}
        <li>{t("Members see data only as their own data access allows.")}</li>
      </ul>
    </Modal>
  );
}

function NewWorkspace({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const save = async () => {
    try {
      await post("/workspaces", { name, description });
      await qc.invalidateQueries({ queryKey: ["workspaces"] });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? t(err.message) : t("Could not save"));
    }
  };
  return (
    <Modal open title={t("New workspace")} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button><Button disabled={!name.trim()} onClick={save}>{t("Create")}</Button></>}>
      {error && <Alert>{error}</Alert>}
      <Field label={t("Name")}><Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder={t("Operations")} /></Field>
      <Field label={t("Description")}><Input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
    </Modal>
  );
}

export default function Workspaces() {
  const { hasRole } = useAuth();
  const qc = useQueryClient();
  const { list, setPick } = useWorkspace();
  const navigate = useNavigate();
  const [editing, setEditing] = useState<Workspace | null>(null);
  const [creating, setCreating] = useState(false);
  const remove = async (w: Workspace) => {
    if (!confirm(t('Delete the workspace "{name}"? Its dashboards and charts stay, as personal items of their owners.', { name: w.name }))) return;
    await del(`/workspaces/${w.id}`);
    await qc.invalidateQueries({ queryKey: ["workspaces"] });
    await qc.invalidateQueries({ queryKey: ["dashboards"] });
  };
  return (
    <>
      <PageHeader title={t("Workspaces")} description={t("Shared places for a team's dashboards and charts.")}
        actions={hasRole("admin") ? <Button onClick={() => setCreating(true)}><Plus size={16} /> {t("New workspace")}</Button> : undefined} />
      {!list.length ? (
        <Card className="p-10 text-center text-sm text-slate-500">
          {hasRole("admin") ? t("No workspaces yet. Create one for each team.") : t("You are not a member of any workspace yet.")}
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {list.map((w) => (
            <Card key={w.id} className="flex flex-col gap-3 p-5">
              <div className="flex items-start gap-3">
                <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-700"><Building2 size={18} /></span>
                <div className="min-w-0 flex-1">
                  <h2 className="truncate text-base font-semibold">{w.name}</h2>
                  {w.description && <p className="line-clamp-2 text-sm text-slate-500">{w.description}</p>}
                </div>
                <Badge tone={w.my_role === "manager" ? "blue" : "slate"}>{roleLabel(w.my_role)}</Badge>
              </div>
              <p className="text-sm text-slate-600">
                {t("{d} dashboards · {c} charts · {m} members", { d: w.dashboards, c: w.charts, m: w.members })}
              </p>
              <div className="mt-auto flex flex-wrap gap-2">
                <Button size="sm" onClick={() => { setPick(w.id); navigate("/dashboards"); }}>{t("Open")}</Button>
                <Button size="sm" variant="secondary" onClick={() => setEditing(w)}>
                  <UserPlus size={14} /> {w.my_role === "manager" ? t("Members and settings") : t("Members")}
                </Button>
                {hasRole("admin") && (
                  <Button size="sm" variant="ghost" className="ms-auto" onClick={() => remove(w)} aria-label={t("Delete")}><Trash2 size={14} /></Button>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}
      {editing && <MembersModal ws={editing} onClose={() => setEditing(null)} />}
      {creating && <NewWorkspace onClose={() => setCreating(false)} />}
    </>
  );
}
