import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Upload } from "lucide-react";
import { useState } from "react";

import {
  Alert, Badge, Button, Card, Field, Input, Modal, PageHeader, Pager, Select, Table, formatDate,
} from "../../components/ui";
import { api, del, get, patch, post } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { ROLES, type AttributeValue, type Group, type Page, type Role, type User } from "../../lib/types";
import { PASSWORD_HINT } from "../ResetPassword";
import { t } from "../../lib/i18n";

const PAGE_SIZE = 25;

// ---------- attribute editor: values with commas become lists ----------
type AttrRow = { key: string; value: string };

const toRows = (attrs: Record<string, AttributeValue>): AttrRow[] =>
  Object.entries(attrs).map(([key, v]) => ({ key, value: Array.isArray(v) ? v.join(", ") : String(v) }));

function toAttrs(rows: AttrRow[]): Record<string, AttributeValue> {
  const out: Record<string, AttributeValue> = {};
  for (const { key, value } of rows) {
    const k = key.trim();
    if (!k || !value.trim()) continue;
    const parts = value.split(",").map((s) => s.trim()).filter(Boolean);
    out[k] = parts.length > 1 ? parts : parts[0];
  }
  return out;
}

function AttributeEditor({ rows, onChange }: { rows: AttrRow[]; onChange: (rows: AttrRow[]) => void }) {
  const set = (i: number, patchRow: Partial<AttrRow>) =>
    onChange(rows.map((r, j) => (j === i ? { ...r, ...patchRow } : r)));
  return (
    <div className="space-y-2">
      {rows.map((row, i) => (
        <div key={i} className="flex gap-2">
          <Input placeholder={t("region")} value={row.key} onChange={(e) => set(i, { key: e.target.value })} className="w-1/3" />
          <Input placeholder={t("UAE, KSA")} value={row.value} onChange={(e) => set(i, { value: e.target.value })} />
          <Button type="button" variant="ghost" aria-label={t("Remove")} onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            <Trash2 size={16} />
          </Button>
        </div>
      ))}
      <Button type="button" variant="secondary" size="sm" onClick={() => onChange([...rows, { key: "", value: "" }])}>
        <Plus size={14} /> {t("Add attribute")}
      </Button>
    </div>
  );
}

// ---------- create / edit form ----------
function UserModal({ user, groups, onClose }: { user: User | "new"; groups: Group[]; onClose: () => void }) {
  const qc = useQueryClient();
  const { user: me } = useAuth();
  const isNew = user === "new";
  const [email, setEmail] = useState(isNew ? "" : user.email);
  const [name, setName] = useState(isNew ? "" : user.name);
  const [role, setRole] = useState<Role>(isNew ? "viewer" : user.role);
  const [password, setPassword] = useState("");
  const [groupIds, setGroupIds] = useState<number[]>(isNew ? [] : user.groups.map((g) => g.id));
  const [attrs, setAttrs] = useState<AttrRow[]>(isNew ? [] : toRows(user.attributes));
  const [invite, setInvite] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = () => qc.invalidateQueries({ queryKey: ["users"] });
  const run = async (fn: () => Promise<unknown>, done?: string) => {
    setError("");
    setNotice("");
    try {
      await fn();
      await refresh();
      if (done) setNotice(done);
      else onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Something went wrong"));
    }
  };

  const save = () =>
    run(() => {
      const common = { name, role, group_ids: groupIds, attributes: toAttrs(attrs), password: password || undefined };
      return isNew
        ? post("/users", { ...common, email, send_invite: invite && !password })
        : patch(`/users/${user.id}`, common);
    });

  return (
    <Modal open wide title={isNew ? t("Add user") : `Edit ${user.name || user.email}`} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button><Button onClick={save}>{t("Save")}</Button></>}>
      {error && <Alert>{error}</Alert>}
      {notice && <Alert tone="green">{notice}</Alert>}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("Email")}>
          <Input type="email" value={email} disabled={!isNew} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label={t("Name")}><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label={t("Role")} hint={ROLES.find((r) => r.value === role)?.help}>
          <Select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {ROLES.map((r) => <option key={r.value} value={r.value}>{t(r.label)}</option>)}
          </Select>
        </Field>
        <Field label={isNew ? t("Password (optional)") : t("Set new password (optional)")} hint={t(PASSWORD_HINT)}>
          <Input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
      </div>
      {isNew && !password && (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={invite} onChange={(e) => setInvite(e.target.checked)} />
          {t("Email an invitation link so they can set their own password")}
        </label>
      )}
      <Field label={t("Groups")}>
        <div className="flex flex-wrap gap-2">
          {groups.length === 0 && <span className="text-sm text-slate-500">{t("No groups yet")}</span>}
          {groups.map((g) => (
            <label key={g.id} className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1 text-sm">
              <input type="checkbox" checked={groupIds.includes(g.id)}
                onChange={(e) => setGroupIds(e.target.checked ? [...groupIds, g.id] : groupIds.filter((id) => id !== g.id))} />
              {g.name}
            </label>
          ))}
        </div>
      </Field>
      <Field label={t("Data access attributes")} hint={t("Used by row-level security, e.g. region = UAE, KSA. Separate several values with commas.")}>
        <AttributeEditor rows={attrs} onChange={setAttrs} />
      </Field>

      {!isNew && user.sign_in_methods.length > 0 && (
        <Field label={t("Linked sign-in methods")}>
          <ul className="space-y-1 text-sm">
            {user.sign_in_methods.map((m) => (
              <li key={m.provider_id} className="flex justify-between gap-4">
                <span><Badge tone="blue">{m.provider_name}</Badge> <span className="text-slate-500">{m.email}</span></span>
                <span className="text-slate-500">{t("last used {v0}", { v0: formatDate(m.last_login_at) })}</span>
              </li>
            ))}
          </ul>
        </Field>
      )}
      {!isNew && (
        <div className="space-y-2 border-t border-slate-200 pt-4">
          <p className="text-sm font-medium text-slate-700">{t("Account actions")}</p>
          <div className="flex flex-wrap gap-2">
            {user.auth_provider === "local" && (
              <Button variant="secondary" size="sm" onClick={() => run(() => post(`/users/${user.id}/send-reset`), "Password reset email sent")}>
                {t("Send password reset")}
              </Button>
            )}
            <Button variant="secondary" size="sm" onClick={() => run(() => post(`/users/${user.id}/sign-out`), "Signed out of all sessions")}>
              {t("Sign out everywhere")}
            </Button>
            {user.locked && (
              <Button variant="secondary" size="sm" onClick={() => run(() => patch(`/users/${user.id}`, { unlock: true }), "Account unlocked")}>
                {t("Unlock")}
              </Button>
            )}
            {user.id !== me?.id && (
              <>
                <Button variant="secondary" size="sm"
                  onClick={() => run(() => patch(`/users/${user.id}`, { is_active: !user.is_active }))}>
                  {user.is_active ? t("Deactivate") : t("Activate")}
                </Button>
                <Button variant="danger" size="sm"
                  onClick={() => confirm(t("Delete {email}? This cannot be undone.", { email: user.email })) && run(() => del(`/users/${user.id}`))}>
                  {t("Delete")}
                </Button>
              </>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

// ---------- CSV import ----------
function ImportModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<{ created: number; updated: number; errors: string[] } | null>(null);
  const [error, setError] = useState("");
  const upload = async () => {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      setResult(await api("/users/import", { method: "POST", form }));
      qc.invalidateQueries({ queryKey: ["users"] });
      qc.invalidateQueries({ queryKey: ["groups"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Import failed"));
    }
  };
  return (
    <Modal open title={t("Import users from CSV")} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>{t("Close")}</Button><Button disabled={!file} onClick={upload}>{t("Import")}</Button></>}>
      <p className="text-sm text-slate-600">
        {t("Columns:")} <code>email</code> {t("(required),")} <code>name</code>, <code>role</code>, <code>groups</code> {t("(separated by ;),")}{" "}
        <code>password</code>{t(", and")} <code>attr_&lt;name&gt;</code> {t("for data-access attributes (several values separated by ;). Existing users are updated by email.")}
      </p>
      <Input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      {error && <Alert>{error}</Alert>}
      {result && (
        <Alert tone={result.errors.length ? "blue" : "green"}>
          {t("Created")} {result.created}{t(", updated")} {result.updated}.
          {result.errors.length > 0 && <ul className="mt-1 list-disc ps-5">{result.errors.map((e) => <li key={e}>{e}</li>)}</ul>}
        </Alert>
      )}
    </Modal>
  );
}

export default function Users() {
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<User | "new" | null>(null);
  const [importing, setImporting] = useState(false);

  const params = new URLSearchParams({ q, page: String(page), page_size: String(PAGE_SIZE) });
  if (role) params.set("role", role);
  const users = useQuery({ queryKey: ["users", q, role, page], queryFn: () => get<Page<User>>(`/users?${params}`) });
  const groups = useQuery({ queryKey: ["groups"], queryFn: () => get<Group[]>("/groups") });

  return (
    <>
      <PageHeader title={t("Users")} description={t("Who can sign in, what they can do, and which data they can see.")}
        actions={<>
          <Button variant="secondary" onClick={() => setImporting(true)}><Upload size={16} /> {t("Import CSV")}</Button>
          <Button onClick={() => setEditing("new")}><Plus size={16} /> {t("Add user")}</Button>
        </>} />
      <Card>
        <div className="flex flex-wrap gap-3 border-b border-slate-200 p-4">
          <Input placeholder={t("Search name or email")} value={q} className="max-w-xs"
            onChange={(e) => { setQ(e.target.value); setPage(1); }} />
          <Select value={role} className="max-w-44" onChange={(e) => { setRole(e.target.value); setPage(1); }}>
            <option value="">{t("All roles")}</option>
            {ROLES.map((r) => <option key={r.value} value={r.value}>{t(r.label)}</option>)}
          </Select>
        </div>
        {users.error && <div className="p-4"><Alert>{(users.error as Error).message}</Alert></div>}
        <Table head={[t("User"), t("Role"), t("Groups"), t("Status"), t("Last sign-in")]}>
          {users.data?.items.map((u) => (
            <tr key={u.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setEditing(u)}>
              <td className="px-4 py-3">
                <div className="font-medium">{u.name || "—"}</div>
                <div className="text-slate-500">{u.email}</div>
              </td>
              <td className="px-4 py-3 capitalize">{u.role}</td>
              <td className="px-4 py-3"><div className="flex flex-wrap gap-1">{u.groups.map((g) => <Badge key={g.id}>{g.name}</Badge>)}</div></td>
              <td className="px-4 py-3">
                {!u.is_active ? <Badge tone="red">{t("Inactive")}</Badge> : u.locked ? <Badge tone="amber">{t("Locked")}</Badge> : <Badge tone="green">{t("Active")}</Badge>}
              </td>
              <td className="px-4 py-3 text-slate-500">{formatDate(u.last_login_at)}</td>
            </tr>
          ))}
        </Table>
        {users.data && <Pager page={page} pageSize={PAGE_SIZE} total={users.data.total} onPage={setPage} />}
      </Card>
      {editing && <UserModal user={editing} groups={groups.data ?? []} onClose={() => setEditing(null)} />}
      {importing && <ImportModal onClose={() => setImporting(false)} />}
    </>
  );
}
