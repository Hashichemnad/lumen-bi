import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Lock, Pencil, Plus, ShieldAlert, ShieldCheck, Trash2, Users, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Alert, Badge, Button, Card, Field as FormField, Input, Modal, Select } from "../../components/ui";
import { del, get, post, put } from "../../lib/api";
import type {
  Dataset, DatasetAccess, DatasetSecurity, Field, Principal, QueryResult, RlsRule, SecurityCheck,
} from "../../lib/types";
import { formatNumber } from "./common";
import { t } from "../../lib/i18n";
import { MaskingCard } from "./QualityTab";

interface Directory {
  users: Principal[];
  groups: Principal[];
}

const message = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return String(value);
}

function Chip({ children, onRemove }: { children: React.ReactNode; onRemove?: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-700">
      {children}
      {onRemove && <button onClick={onRemove} className="text-slate-400 hover:text-slate-700" aria-label={t("Remove")}><X size={12} /></button>}
    </span>
  );
}

// ------------------------------------------------------------------ who can use the dataset
function AccessCard({ datasetId, access, directory, canEdit }: {
  datasetId: number; access: DatasetAccess; directory?: Directory; canEdit: boolean;
}) {
  const qc = useQueryClient();
  const [everyone, setEveryone] = useState(access.everyone);
  const [userIds, setUserIds] = useState(access.user_ids);
  const [groupIds, setGroupIds] = useState(access.group_ids);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    setEveryone(access.everyone);
    setUserIds(access.user_ids);
    setGroupIds(access.group_ids);
  }, [access]);
  const changed = everyone !== access.everyone || userIds.join() !== access.user_ids.join() || groupIds.join() !== access.group_ids.join();
  const userName = (id: number) => {
    const u = directory?.users.find((x) => x.id === id) ?? access.users.find((x) => x.id === id);
    return u ? u.name || u.email : `User ${id}`;
  };
  const groupName = (id: number) => (directory?.groups.find((x) => x.id === id) ?? access.groups.find((x) => x.id === id))?.name ?? `Group ${id}`;
  const save = async () => {
    setError("");
    try {
      await put(`/datasets/${datasetId}/access`, { everyone, user_ids: userIds, group_ids: groupIds });
      await qc.invalidateQueries({ queryKey: ["security", datasetId] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(message(err, "Could not save"));
    }
  };
  return (
    <Card className="p-5">
      <h3 className="flex items-center gap-2 font-semibold"><Users size={16} /> {t("Who can use this dataset")}</h3>
      <p className="mt-1 text-sm text-slate-500">{t("Creators and admins can always use it. Others need access here (or through a shared dashboard, coming later).")}</p>
      {error && <div className="mt-3"><Alert>{error}</Alert></div>}
      <label className="mt-4 flex items-center gap-2 text-sm">
        <input type="checkbox" checked={everyone} disabled={!canEdit} onChange={(e) => setEveryone(e.target.checked)} />
        {t("Everyone who can sign in")}
      </label>
      {!everyone && (
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <div>
            <div className="mb-1.5 text-xs font-medium text-slate-600">{t("People")}</div>
            <div className="flex flex-wrap gap-1.5">
              {userIds.map((id) => <Chip key={id} onRemove={canEdit ? () => setUserIds(userIds.filter((x) => x !== id)) : undefined}>{userName(id)}</Chip>)}
              {!userIds.length && <span className="text-xs text-slate-400">{t("No one")}</span>}
            </div>
            {canEdit && (
              <Select className="mt-2 py-1.5" value="" onChange={(e) => e.target.value && setUserIds([...userIds, Number(e.target.value)])}>
                <option value="">{t("Add a person…")}</option>
                {directory?.users.filter((u) => !userIds.includes(u.id) && u.role !== "admin" && u.role !== "creator").map((u) => (
                  <option key={u.id} value={u.id}>{u.name ? `${u.name} (${u.email})` : u.email}</option>
                ))}
              </Select>
            )}
          </div>
          <div>
            <div className="mb-1.5 text-xs font-medium text-slate-600">{t("Groups")}</div>
            <div className="flex flex-wrap gap-1.5">
              {groupIds.map((id) => <Chip key={id} onRemove={canEdit ? () => setGroupIds(groupIds.filter((x) => x !== id)) : undefined}>{groupName(id)}</Chip>)}
              {!groupIds.length && <span className="text-xs text-slate-400">{t("No groups")}</span>}
            </div>
            {canEdit && (
              <Select className="mt-2 py-1.5" value="" onChange={(e) => e.target.value && setGroupIds([...groupIds, Number(e.target.value)])}>
                <option value="">{t("Add a group…")}</option>
                {directory?.groups.filter((g) => !groupIds.includes(g.id)).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
              </Select>
            )}
          </div>
        </div>
      )}
      {canEdit && (
        <div className="mt-4 flex items-center gap-3">
          <Button onClick={save} disabled={!changed}>{t("Save access")}</Button>
          {saved && <span className="text-sm text-green-700">{t("Saved")}</span>}
        </div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ rule editor
function RuleModal({ datasetId, existing, attributes, directory, onClose }: {
  datasetId: number; existing?: RlsRule; attributes: string[]; directory?: Directory; onClose: () => void;
}) {
  const qc = useQueryClient();
  const fields = useQuery({ queryKey: ["fields", datasetId], queryFn: () => get<Field[]>(`/datasets/${datasetId}/fields`) });
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => get<Dataset[]>("/datasets") });
  const [name, setName] = useState(existing?.name ?? "");
  const [kind, setKind] = useState<RlsRule["kind"]>(existing?.kind ?? "attribute");
  const [cfg, setCfg] = useState<RlsRule["config"]>(existing?.config ?? { field: "", attribute: "", ignore_case: true, match: "email" });
  const [exempt, setExempt] = useState<number[]>(existing?.exempt_groups ?? []);
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const set = (changes: Partial<RlsRule["config"]>) => setCfg({ ...cfg, ...changes });

  const usable = (fields.data ?? []).filter((f) => !f.missing && !f.error && !f.is_aggregate);
  const accessTables = (datasets.data ?? []).filter((d) => d.id !== datasetId && d.current_version > 0);
  const table = accessTables.find((d) => d.id === cfg.dataset);
  const tableColumns = table?.columns.map((c) => c.name) ?? [];

  const save = async () => {
    setError("");
    setSaving(true);
    const body = { name, kind, config: cfg, exempt_groups: exempt, enabled };
    try {
      if (existing) await put(`/datasets/${datasetId}/security/rules/${existing.id}`, body);
      else await post(`/datasets/${datasetId}/security/rules`, body);
      await qc.invalidateQueries({ queryKey: ["security", datasetId] });
      await qc.invalidateQueries({ queryKey: ["preview", datasetId] });
      await qc.invalidateQueries({ queryKey: ["security-check", datasetId] });
      onClose();
    } catch (err) {
      setError(message(err, "Could not save the rule"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open wide title={existing ? t("Edit rule") : t("Add a row-level security rule")} onClose={onClose}
      footer={<>
        <Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button>
        <Button onClick={save} disabled={saving || !cfg.field}>{saving ? t("Saving…") : t("Save rule")}</Button>
      </>}>
      <div className="space-y-4">
        {error && <Alert>{error}</Alert>}
        <FormField label={t("Name (optional)")}><Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("e.g. Sales reps see their region")} /></FormField>
        <div className="grid grid-cols-2 gap-2">
          {([
            ["attribute", "User attribute", "Match a field to a value on the user, e.g. Region = user's region"],
            ["entitlement", "Access table", "Look up allowed values per user in another dataset"],
          ] as const).map(([k, title, hint]) => (
            <button key={k} onClick={() => setKind(k)}
              className={`rounded-lg border p-3 text-start text-sm ${kind === k ? "border-brand-600 bg-brand-50" : "border-slate-200 hover:border-slate-300"}`}>
              <div className="font-medium">{title}</div>
              <div className="mt-0.5 text-xs text-slate-500">{hint}</div>
            </button>
          ))}
        </div>
        <FormField label={t("Only show rows where this field…")}>
          <Select value={cfg.field} onChange={(e) => set({ field: e.target.value })}>
            <option value="">{t("Choose a field")}</option>
            {usable.map((f) => <option key={f.id} value={f.name}>{f.label || f.name}</option>)}
          </Select>
        </FormField>
        {kind === "attribute" ? (
          <FormField label={t("…is one of the user's values for this attribute")}
            hint={t("Set attributes on each user (Admin → Users) or copy them from Microsoft / UAE PASS. Built in: email, name, user_id, groups.")}>
            <Input list="rls-attributes" value={cfg.attribute ?? ""} onChange={(e) => set({ attribute: e.target.value })} placeholder={t("e.g. region")} />
            <datalist id="rls-attributes">{attributes.map((a) => <option key={a} value={a} />)}</datalist>
          </FormField>
        ) : (
          <div className="space-y-4 rounded-lg bg-slate-50 p-3">
            <FormField label={t("…is listed for the user in this dataset")}>
              <Select value={cfg.dataset ?? ""} onChange={(e) => set({ dataset: Number(e.target.value) || undefined, user_column: "", value_column: "" })}>
                <option value="">{t("Choose the access table")}</option>
                {accessTables.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </Select>
            </FormField>
            {table && (
              <div className="grid grid-cols-2 gap-3">
                <FormField label={t("Column with the user")}>
                  <Select value={cfg.user_column ?? ""} onChange={(e) => set({ user_column: e.target.value })}>
                    <option value="">{t("Choose")}</option>
                    {tableColumns.map((c) => <option key={c} value={c}>{c}</option>)}
                  </Select>
                </FormField>
                <FormField label={t("Matched with the user's")}>
                  <Input list="rls-attributes" value={cfg.match ?? "email"} onChange={(e) => set({ match: e.target.value })} />
                  <datalist id="rls-attributes">{attributes.map((a) => <option key={a} value={a} />)}</datalist>
                </FormField>
                <FormField label={t("Column with allowed values")}>
                  <Select value={cfg.value_column ?? ""} onChange={(e) => set({ value_column: e.target.value })}>
                    <option value="">{t("Choose")}</option>
                    {tableColumns.map((c) => <option key={c} value={c}>{c}</option>)}
                  </Select>
                </FormField>
                <FormField label={t("Value meaning all rows (optional)")}>
                  <Input value={cfg.all_value ?? ""} onChange={(e) => set({ all_value: e.target.value })} placeholder={t("e.g. * or ALL")} />
                </FormField>
              </div>
            )}
          </div>
        )}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={cfg.ignore_case ?? true} onChange={(e) => set({ ignore_case: e.target.checked })} />
          {t("Ignore upper/lower case and spaces around text")}
        </label>
        <FormField label={t("Groups that see all rows (optional)")} hint={t("For example Management. Admins always see all rows.")}>
          <div className="flex flex-wrap gap-1.5">
            {exempt.map((g) => <Chip key={g} onRemove={() => setExempt(exempt.filter((x) => x !== g))}>{directory?.groups.find((x) => x.id === g)?.name ?? g}</Chip>)}
          </div>
          <Select className="mt-2" value="" onChange={(e) => e.target.value && setExempt([...exempt, Number(e.target.value)])}>
            <option value="">{t("Add a group…")}</option>
            {directory?.groups.filter((g) => !exempt.includes(g.id)).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
          </Select>
        </FormField>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> {t("Rule is on")}
        </label>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ check what someone sees
const STATUS: Record<string, { tone: "green" | "blue" | "red" | "amber"; label: string }> = {
  applies: { tone: "blue", label: "Filters rows" },
  exempt: { tone: "green", label: "Exempt" },
  all: { tone: "green", label: "All rows" },
  blocked: { tone: "red", label: "Blocks all rows" },
};

function CheckCard({ datasetId, directory }: { datasetId: number; directory?: Directory }) {
  const [userId, setUserId] = useState<number | null>(null);
  const check = useQuery({
    queryKey: ["security-check", datasetId, userId],
    queryFn: () => get<SecurityCheck>(`/datasets/${datasetId}/security/check?user_id=${userId}`),
    enabled: userId !== null,
  });
  const rows = useQuery({
    queryKey: ["security-check", datasetId, userId, "rows"],
    queryFn: () => post<QueryResult>("/query", { dataset: datasetId, mode: "rows", limit: 20, as_user_id: userId }),
    enabled: userId !== null && !!check.data?.has_access,
    retry: false,
  });
  const c = check.data;
  return (
    <Card className="p-5">
      <h3 className="flex items-center gap-2 font-semibold"><Eye size={16} /> {t("Check what someone sees")}</h3>
      <p className="mt-1 text-sm text-slate-500">{t("Choose a person to see which rules apply to them and the rows they would get.")}</p>
      <Select className="mt-3 max-w-md" value={userId ?? ""} onChange={(e) => setUserId(e.target.value ? Number(e.target.value) : null)}>
        <option value="">{t("Choose a person…")}</option>
        {directory?.users.map((u) => <option key={u.id} value={u.id}>{u.name ? `${u.name} (${u.email})` : u.email} · {u.role}</option>)}
      </Select>
      {check.error && <div className="mt-3"><Alert>{(check.error as Error).message}</Alert></div>}
      {c && (
        <div className="mt-4 space-y-3">
          {!c.has_access && <Alert>{t("{email} cannot use this dataset. Give them access above first.", { email: c.user.email })}</Alert>}
          {c.bypass ? (
            <Alert tone="blue">{t("{email} is an admin and sees every row.", { email: c.user.email })}</Alert>
          ) : (
            <p className="text-sm">
              <span className="font-semibold">{c.visible_rows === null ? "—" : formatNumber(c.visible_rows)}</span> {t("of")} {formatNumber(c.total_rows)} {t("rows visible")}
              {!c.rules.length && <span className="text-slate-500"> {t("(no rules on this dataset)")}</span>}
            </p>
          )}
          {c.rules.map((r) => (
            <div key={`${r.rule_id}-${r.inherited_from?.id ?? 0}`} className="flex flex-wrap items-start gap-2 text-sm">
              <Badge tone={STATUS[r.status].tone}>{t(STATUS[r.status].label)}</Badge>
              <span className="font-medium">{r.name}</span>
              {r.inherited_from && <span className="text-xs text-slate-500">{t("from {name}", { name: r.inherited_from.name })}</span>}
              {r.status === "applies" && (
                <span className="text-slate-600">
                  {t("values:")} {r.values.slice(0, 10).map(cell).join(", ")}{r.value_count > 10 && ` and ${r.value_count - 10} more`}
                </span>
              )}
              {r.reason && <span className="text-slate-500">{r.reason}</span>}
            </div>
          ))}
          {rows.data && (
            <div className="max-h-80 overflow-auto rounded-lg border border-slate-200">
              <table className="min-w-full text-sm">
                <thead className="sticky top-0 bg-slate-50">
                  <tr>{rows.data.columns.map((col) => <th key={col.key} className="whitespace-nowrap px-3 py-2 text-start text-xs font-semibold text-slate-600">{t(col.label)}</th>)}</tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {rows.data.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} className="whitespace-nowrap px-3 py-1.5 tabular-nums">{cell(v)}</td>)}</tr>)}
                  {!rows.data.rows.length && <tr><td className="px-3 py-4 text-center text-slate-500" colSpan={rows.data.columns.length || 1}>{t("No rows")}</td></tr>}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ the tab
/** Sensitive data is only ever sent to self-hosted AI models (never to cloud AI services). */
function SensitiveCard({ dataset, canEdit }: { dataset: Dataset; canEdit: boolean }) {
  const qc = useQueryClient();
  const [error, setError] = useState("");
  const on = dataset.sensitive || dataset.inherited_sensitive;
  const toggle = async () => {
    setError("");
    try {
      await put(`/datasets/${dataset.id}/sensitive`, { sensitive: !dataset.sensitive });
      await qc.invalidateQueries({ queryKey: ["dataset"] });
    } catch (err) {
      setError(message(err, "Could not save"));
    }
  };
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="flex items-center gap-2 font-semibold"><ShieldAlert size={16} /> {t("Sensitive data")}</h3>
          <p className="mt-1 text-sm text-slate-500">
            {dataset.inherited_sensitive
              ? t("Built from sensitive data, so it is sensitive too. AI questions about it go only to self-hosted models.")
              : on
                ? t("AI questions about this data go only to self-hosted models. If none is set up, AI is not available for it.")
                : t("Mark data as sensitive to keep it away from cloud AI services. Only self-hosted models will see it.")}
          </p>
        </div>
        {canEdit && !dataset.inherited_sensitive && (
          <label className="flex shrink-0 items-center gap-2 text-sm font-medium">
            <input type="checkbox" checked={dataset.sensitive} onChange={toggle} className="h-4 w-4" />
            {t("Sensitive")}
          </label>
        )}
        {dataset.inherited_sensitive && <Badge tone="amber">{t("Sensitive")}</Badge>}
      </div>
      {error && <div className="mt-3"><Alert>{error}</Alert></div>}
    </Card>
  );
}

export default function SecurityTab({ datasetId, canEdit, columns = [], dataset }: { datasetId: number; canEdit: boolean; columns?: string[]; dataset?: Dataset }) {
  const qc = useQueryClient();
  const security = useQuery({ queryKey: ["security", datasetId], queryFn: () => get<DatasetSecurity>(`/datasets/${datasetId}/security`) });
  const directory = useQuery({ queryKey: ["directory"], queryFn: () => get<Directory>("/directory") });
  const [editing, setEditing] = useState<RlsRule | "new" | null>(null);
  const [error, setError] = useState("");

  if (!security.data) return security.error ? <Alert>{(security.error as Error).message}</Alert> : null;
  const s = security.data;
  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: ["security", datasetId] });
    await qc.invalidateQueries({ queryKey: ["preview", datasetId] });
    await qc.invalidateQueries({ queryKey: ["security-check", datasetId] });
  };
  const toggle = async (r: RlsRule) => {
    setError("");
    try {
      await put(`/datasets/${datasetId}/security/rules/${r.id}`, { ...r, enabled: !r.enabled });
      await refresh();
    } catch (err) {
      setError(message(err, "Could not change the rule"));
    }
  };
  const remove = async (r: RlsRule) => {
    if (!confirm(t("Delete the rule \"{v0}\"? People it filtered may see more rows.", { v0: r.name || r.summary }))) return;
    try {
      await del(`/datasets/${datasetId}/security/rules/${r.id}`);
      await refresh();
    } catch (err) {
      setError(message(err, "Could not delete the rule"));
    }
  };
  const total = s.rules.filter((r) => r.enabled).length + s.inherited.length;

  return (
    <div className="space-y-4">
      <AccessCard datasetId={datasetId} access={s.access} directory={directory.data} canEdit={canEdit} />

      <Card className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="flex items-center gap-2 font-semibold"><Lock size={16} /> {t("Row-level security")}</h3>
            <p className="mt-1 text-sm text-slate-500">
              {total
                ? t("Everyone except admins sees only the rows these rules allow. All rules apply together. Anyone the rules cannot place (no matching value) sees no rows.")
                : t("No rules: everyone who can use this dataset sees all of its rows.")}
            </p>
          </div>
          {canEdit && <div className="shrink-0"><Button onClick={() => setEditing("new")}><Plus size={16} /> {t("Add rule")}</Button></div>}
        </div>
        {error && <div className="mt-3"><Alert>{error}</Alert></div>}
        {(s.rules.length > 0 || s.inherited.length > 0) && (
          <div className="mt-4 divide-y divide-slate-100 rounded-lg border border-slate-200">
            {s.rules.map((r) => (
              <div key={r.id} className={`flex flex-wrap items-center gap-3 px-4 py-3 text-sm ${r.enabled ? "" : "bg-slate-50 text-slate-500"}`}>
                <ShieldCheck size={16} className={r.enabled ? "text-brand-600" : "text-slate-400"} />
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{r.name || r.summary}</div>
                  <div className="text-xs text-slate-500">
                    {r.name && <>{r.summary} · </>}
                    {r.kind === "attribute" ? t("User attribute") : t("Access table")}
                    {r.exempt_group_names.length > 0 && <> {t("· all rows for")} {r.exempt_group_names.join(", ")}</>}
                  </div>
                </div>
                {!r.enabled && <Badge>{t("Off")}</Badge>}
                {canEdit && (
                  <div className="flex gap-1">
                    <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => toggle(r)}>{r.enabled ? t("Turn off") : t("Turn on")}</Button>
                    <button onClick={() => setEditing(r)} className="rounded p-1.5 text-slate-500 hover:bg-slate-100" aria-label={t("Edit")}><Pencil size={14} /></button>
                    <button onClick={() => remove(r)} className="rounded p-1.5 text-slate-500 hover:bg-red-50 hover:text-red-600" aria-label={t("Delete")}><Trash2 size={14} /></button>
                  </div>
                )}
              </div>
            ))}
            {s.inherited.map((i) => (
              <div key={`i-${i.rule.id}-${i.dataset.id}`} className="flex flex-wrap items-center gap-3 bg-slate-50/50 px-4 py-3 text-sm">
                <ShieldCheck size={16} className="text-slate-400" />
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{i.rule.name || i.rule.summary}</div>
                  <div className="text-xs text-slate-500">{t("From {name}, which this dataset is built from. Change it there.", { name: i.dataset.name })}</div>
                </div>
                {!i.mapped && <Badge tone="red">{t("Field missing: blocks all rows")}</Badge>}
                <Badge>{t("Inherited")}</Badge>
              </div>
            ))}
          </div>
        )}
      </Card>

      <MaskingCard datasetId={datasetId} columns={columns} canEdit={canEdit} />

      {dataset && <SensitiveCard dataset={dataset} canEdit={canEdit} />}

      {canEdit && <CheckCard datasetId={datasetId} directory={directory.data} />}

      {editing && (
        <RuleModal datasetId={datasetId} existing={editing === "new" ? undefined : editing}
          attributes={s.attributes} directory={directory.data} onClose={() => setEditing(null)} />
      )}
    </div>
  );
}
