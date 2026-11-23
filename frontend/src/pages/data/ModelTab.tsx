import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Calculator, EyeOff, Layers, Pencil, Plus, Ruler, SlidersHorizontal, Trash2 } from "lucide-react";
import { useState } from "react";

import { Alert, Badge, Button, Card, Field as FormField, Input, Modal, Select } from "../../components/ui";
import { del, get, patch, post, put } from "../../lib/api";
import type { Field, FieldFormat, Hierarchy, Parameter } from "../../lib/types";
import { BinModal, CalculatedFieldModal, GroupModal, TYPE_LABELS } from "./FieldEditors";

const FORMAT_LABELS: Record<FieldFormat["type"], string> = {
  number: "Number", currency: "Currency", percent: "Percent", date: "Date", datetime: "Date & time", text: "Text",
};
const AGG_LABELS: Record<string, string> = {
  sum: "Sum", avg: "Average", count: "Count", count_distinct: "Count distinct", min: "Minimum", max: "Maximum",
  median: "Median", none: "None",
};
const KIND_ICONS: Record<string, React.ReactNode> = {
  calculated: <Calculator size={14} />, group: <Layers size={14} />, bin: <Ruler size={14} />,
};

function useModel(datasetId: number) {
  const fields = useQuery({ queryKey: ["fields", datasetId], queryFn: () => get<Field[]>(`/datasets/${datasetId}/fields`) });
  const hierarchies = useQuery({ queryKey: ["hierarchies", datasetId], queryFn: () => get<Hierarchy[]>(`/datasets/${datasetId}/hierarchies`) });
  const params = useQuery({ queryKey: ["parameters", datasetId], queryFn: () => get<Parameter[]>(`/datasets/${datasetId}/parameters`) });
  return { fields, hierarchies, params };
}

function FieldRow({ f, datasetId, canEdit, onEdit, onError }: {
  f: Field; datasetId: number; canEdit: boolean; onEdit: (f: Field) => void; onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const [label, setLabel] = useState(f.label);
  const save = async (changes: Record<string, unknown>) => {
    try {
      await patch(`/datasets/${datasetId}/fields/${f.id}`, changes);
      await qc.invalidateQueries({ queryKey: ["fields", datasetId] });
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not save");
    }
  };
  const remove = async () => {
    if (!confirm(`Delete field "${f.label || f.name}"?`)) return;
    try {
      await del(`/datasets/${datasetId}/fields/${f.id}`);
      await qc.invalidateQueries({ queryKey: ["fields", datasetId] });
      await qc.invalidateQueries({ queryKey: ["preview", datasetId] });
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not delete");
    }
  };
  const fmt = f.format?.type ?? "text";
  return (
    <tr className={f.hidden || f.missing ? "bg-slate-50/60 text-slate-500" : ""}>
      <td className="px-3 py-2">
        <div className="flex items-center gap-1.5">
          {KIND_ICONS[f.kind] && <span className="text-brand-600" title={f.kind}>{KIND_ICONS[f.kind]}</span>}
          <Input value={label} disabled={!canEdit} className="min-w-44 py-1.5"
            onChange={(e) => setLabel(e.target.value)} onBlur={() => label !== f.label && save({ label })} />
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-1 pl-0.5">
          <code className="text-[11px] text-slate-500">{f.name}</code>
          {f.missing && <Badge tone="amber">not in source</Badge>}
          {f.error && <Badge tone="red">broken</Badge>}
          {f.is_aggregate && <Badge tone="blue">aggregate</Badge>}
        </div>
        {f.error && <div className="mt-0.5 max-w-xs text-xs text-red-700">{f.error}</div>}
        {f.formula && <div className="mt-0.5 max-w-xs truncate font-mono text-[11px] text-slate-500" title={f.formula}>{f.formula}</div>}
      </td>
      <td className="px-3 py-2 text-xs text-slate-600">{TYPE_LABELS[f.data_type] ?? f.data_type}</td>
      <td className="px-3 py-2">
        <div className="w-36">
          <Select className="py-1.5" value={f.role} disabled={!canEdit || f.is_aggregate} onChange={(e) => save({ role: e.target.value })}>
            <option value="dimension">Dimension</option>
            <option value="measure">Measure</option>
          </Select>
        </div>
      </td>
      <td className="px-3 py-2">
        <div className="flex items-center gap-1">
          <div className="w-32"><Select className="py-1.5" value={fmt} disabled={!canEdit}
            onChange={(e) => save({ format: { ...f.format, type: e.target.value, ...(e.target.value === "currency" && !f.format.currency ? { currency: "AED" } : {}) } })}>
            {Object.entries(FORMAT_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select></div>
          {["number", "currency", "percent"].includes(fmt) && (
            <div className="w-16"><Input type="number" min="0" max="6" className="py-1.5" title="Decimals" value={f.format.decimals ?? 2} disabled={!canEdit}
              onChange={(e) => save({ format: { ...f.format, decimals: Number(e.target.value) } })} /></div>
          )}
          {fmt === "currency" && (
            <div className="w-16"><Input className="py-1.5 uppercase" title="Currency" defaultValue={f.format.currency ?? "AED"} disabled={!canEdit} maxLength={3}
              onBlur={(e) => save({ format: { ...f.format, currency: e.target.value.toUpperCase() || "AED" } })} /></div>
          )}
        </div>
      </td>
      <td className="px-3 py-2">
        {f.role === "measure" && !f.is_aggregate ? (
          <div className="w-36"><Select className="py-1.5" value={f.default_agg} disabled={!canEdit} onChange={(e) => save({ default_agg: e.target.value })}>
            {Object.entries(AGG_LABELS).filter(([k]) => k !== "none").map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select></div>
        ) : <span className="text-xs text-slate-400">—</span>}
      </td>
      <td className="px-3 py-2 text-center">
        <input type="checkbox" title="Hide from chart builders" checked={f.hidden} disabled={!canEdit} onChange={(e) => save({ hidden: e.target.checked })} />
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-right">
        {canEdit && f.kind !== "column" && <Button variant="ghost" size="sm" aria-label="Edit" onClick={() => onEdit(f)}><Pencil size={14} /></Button>}
        {canEdit && (f.kind !== "column" || f.missing) && (
          <Button variant="ghost" size="sm" aria-label="Delete" title={f.used_by.length ? `Used by ${f.used_by.join(", ")}` : "Delete"} onClick={remove}><Trash2 size={14} /></Button>
        )}
      </td>
    </tr>
  );
}

function HierarchyModal({ datasetId, fields, existing, onClose }: { datasetId: number; fields: Field[]; existing?: Hierarchy; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(existing?.name ?? "");
  const [levels, setLevels] = useState<string[]>(existing?.levels ?? []);
  const [error, setError] = useState("");
  const options = fields.filter((f) => !f.missing && f.role === "dimension" && !levels.includes(f.name));
  const move = (i: number, d: number) => {
    const next = [...levels];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    setLevels(next);
  };
  const label = (n: string) => fields.find((f) => f.name === n)?.label || n;
  const save = async () => {
    setError("");
    try {
      if (existing) await put(`/datasets/${datasetId}/hierarchies/${existing.id}`, { name, levels });
      else await post(`/datasets/${datasetId}/hierarchies`, { name, levels });
      await qc.invalidateQueries({ queryKey: ["hierarchies", datasetId] });
      await qc.invalidateQueries({ queryKey: ["fields", datasetId] });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    }
  };
  return (
    <Modal open title={existing ? `Edit ${existing.name}` : "New hierarchy"} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button onClick={save} disabled={!name.trim() || levels.length < 2}>Save</Button></>}>
      {error && <Alert>{error}</Alert>}
      <p className="text-sm text-slate-600">A drill path from broad to detailed, e.g. Country → Emirate → Branch. Dates drill year → quarter → month → day on their own.</p>
      <FormField label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Geography" /></FormField>
      <ol className="space-y-1">
        {levels.map((lv, i) => (
          <li key={lv} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-1.5 text-sm">
            <span className="w-5 text-slate-400">{i + 1}.</span>
            <span className="flex-1">{label(lv)}</span>
            <Button variant="ghost" size="sm" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Up"><ArrowUp size={14} /></Button>
            <Button variant="ghost" size="sm" disabled={i === levels.length - 1} onClick={() => move(i, 1)} aria-label="Down"><ArrowDown size={14} /></Button>
            <Button variant="ghost" size="sm" onClick={() => setLevels(levels.filter((x) => x !== lv))} aria-label="Remove"><Trash2 size={14} /></Button>
          </li>
        ))}
      </ol>
      <Select value="" onChange={(e) => e.target.value && setLevels([...levels, e.target.value])}>
        <option value="">Add a level…</option>
        {options.map((f) => <option key={f.id} value={f.name}>{f.label || f.name}</option>)}
      </Select>
    </Modal>
  );
}

function ParameterModal({ datasetId, existing, onClose }: { datasetId: number; existing?: Parameter; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(existing?.name ?? "");
  const [type, setType] = useState<Parameter["data_type"]>(existing?.data_type ?? "number");
  const [value, setValue] = useState(existing?.default_value == null ? "" : String(existing.default_value));
  const [allowed, setAllowed] = useState((existing?.allowed_values ?? []).map(String).join(", "));
  const [description, setDescription] = useState(existing?.description ?? "");
  const [error, setError] = useState("");
  const save = async () => {
    setError("");
    const body = { name, data_type: type, description, default_value: value || null,
      allowed_values: allowed.split(",").map((s) => s.trim()).filter(Boolean) };
    try {
      if (existing) await put(`/datasets/${datasetId}/parameters/${existing.id}`, body);
      else await post(`/datasets/${datasetId}/parameters`, body);
      await qc.invalidateQueries({ queryKey: ["parameters", datasetId] });
      await qc.invalidateQueries({ queryKey: ["fields", datasetId] });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    }
  };
  return (
    <Modal open title={existing ? `Edit @${existing.name}` : "New parameter"} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button onClick={save} disabled={!name.trim()}>Save</Button></>}>
      {error && <Alert>{error}</Alert>}
      <p className="text-sm text-slate-600">A value people can change on a dashboard, used in formulas as @Name, e.g. an exchange rate or a target.</p>
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="FX Rate" /></FormField>
        <FormField label="Type">
          <Select value={type} onChange={(e) => setType(e.target.value as Parameter["data_type"])}>
            <option value="number">Number</option><option value="integer">Whole number</option>
            <option value="string">Text</option><option value="date">Date</option><option value="boolean">Yes/No</option>
          </Select>
        </FormField>
        <FormField label="Default value"><Input type={type === "date" ? "date" : "text"} value={value} onChange={(e) => setValue(e.target.value)} /></FormField>
        <FormField label="Allowed values (optional)" hint="Comma-separated. Empty = any value."><Input value={allowed} onChange={(e) => setAllowed(e.target.value)} /></FormField>
      </div>
      <FormField label="Description (optional)"><Input value={description} onChange={(e) => setDescription(e.target.value)} /></FormField>
    </Modal>
  );
}

export default function ModelTab({ datasetId, canEdit }: { datasetId: number; canEdit: boolean }) {
  const qc = useQueryClient();
  const { fields, hierarchies, params } = useModel(datasetId);
  const [editing, setEditing] = useState<{ kind: "calculated" | "group" | "bin"; field?: Field } | null>(null);
  const [hierarchy, setHierarchy] = useState<Hierarchy | "new" | null>(null);
  const [parameter, setParameter] = useState<Parameter | "new" | null>(null);
  const [error, setError] = useState("");
  const [showHidden, setShowHidden] = useState(true);
  const all = fields.data ?? [];
  const list = showHidden ? all : all.filter((f) => !f.hidden);

  const removeHierarchy = async (h: Hierarchy) => {
    if (!confirm(`Delete hierarchy "${h.name}"?`)) return;
    await del(`/datasets/${datasetId}/hierarchies/${h.id}`);
    await qc.invalidateQueries({ queryKey: ["hierarchies", datasetId] });
    await qc.invalidateQueries({ queryKey: ["fields", datasetId] });
  };
  const removeParameter = async (p: Parameter) => {
    if (!confirm(`Delete parameter @${p.name}?`)) return;
    try {
      await del(`/datasets/${datasetId}/parameters/${p.id}`);
      await qc.invalidateQueries({ queryKey: ["parameters", datasetId] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete");
    }
  };

  if (fields.isLoading) return <p className="text-sm text-slate-500">Loading…</p>;
  if (!all.length) return <Card className="p-6 text-center text-sm text-slate-500">Fields appear after the first successful sync.</Card>;

  return (
    <div className="space-y-6">
      {error && <Alert><span className="flex justify-between gap-4">{error}<button onClick={() => setError("")}>×</button></span></Alert>}
      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 p-3">
          <h3 className="mr-auto font-semibold">Fields <span className="text-sm font-normal text-slate-500">· labels, roles and formats people see in charts</span></h3>
          <label className="flex items-center gap-1.5 text-xs text-slate-600"><input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} /> <EyeOff size={12} /> show hidden</label>
          {canEdit && <>
            <Button size="sm" onClick={() => setEditing({ kind: "calculated" })}><Calculator size={14} /> Calculated field</Button>
            <Button size="sm" variant="secondary" onClick={() => setEditing({ kind: "group" })}><Layers size={14} /> Group</Button>
            <Button size="sm" variant="secondary" onClick={() => setEditing({ kind: "bin" })}><Ruler size={14} /> Bins</Button>
          </>}
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
              <tr><th className="px-3 py-2">Field</th><th className="px-3 py-2">Type</th><th className="px-3 py-2">Role</th>
                <th className="px-3 py-2">Format</th><th className="px-3 py-2">Default total</th><th className="px-3 py-2">Hidden</th><th /></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {list.map((f) => (
                <FieldRow key={`${f.id}-${f.label}-${f.format?.type}`} f={f} datasetId={datasetId} canEdit={canEdit} onError={setError}
                  onEdit={(x) => setEditing({ kind: x.kind as "calculated" | "group" | "bin", field: x })} />
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="p-4">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="font-semibold">Hierarchies</h3>
            {canEdit && <Button size="sm" variant="secondary" onClick={() => setHierarchy("new")}><Plus size={14} /> Hierarchy</Button>}
          </div>
          {hierarchies.data?.length ? (
            <ul className="space-y-2 text-sm">
              {hierarchies.data.map((h) => (
                <li key={h.id} className="flex items-center gap-2">
                  <span className="font-medium">{h.name}</span>
                  <span className="flex-1 truncate text-slate-500">{h.levels.map((lv) => all.find((f) => f.name === lv)?.label || lv).join(" → ")}</span>
                  {canEdit && <><Button variant="ghost" size="sm" onClick={() => setHierarchy(h)} aria-label="Edit"><Pencil size={14} /></Button>
                    <Button variant="ghost" size="sm" onClick={() => removeHierarchy(h)} aria-label="Delete"><Trash2 size={14} /></Button></>}
                </li>
              ))}
            </ul>
          ) : <p className="text-sm text-slate-500">None yet. Dates drill down by year, quarter, month and day automatically.</p>}
        </Card>
        <Card className="p-4">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="flex items-center gap-1.5 font-semibold"><SlidersHorizontal size={16} /> Parameters</h3>
            {canEdit && <Button size="sm" variant="secondary" onClick={() => setParameter("new")}><Plus size={14} /> Parameter</Button>}
          </div>
          {params.data?.length ? (
            <ul className="space-y-2 text-sm">
              {params.data.map((p) => (
                <li key={p.id} className="flex items-center gap-2">
                  <code className="text-brand-700">@{p.name}</code>
                  <span className="flex-1 text-slate-500">{TYPE_LABELS[p.data_type] ?? p.data_type} · default {String(p.default_value ?? "—")}
                    {p.allowed_values.length > 0 && ` · one of ${p.allowed_values.join(", ")}`}</span>
                  {canEdit && <><Button variant="ghost" size="sm" onClick={() => setParameter(p)} aria-label="Edit"><Pencil size={14} /></Button>
                    <Button variant="ghost" size="sm" onClick={() => removeParameter(p)} aria-label="Delete"><Trash2 size={14} /></Button></>}
                </li>
              ))}
            </ul>
          ) : <p className="text-sm text-slate-500">None yet.</p>}
        </Card>
      </div>

      {editing?.kind === "calculated" && <CalculatedFieldModal datasetId={datasetId} fields={all} params={params.data ?? []} existing={editing.field} onClose={() => setEditing(null)} />}
      {editing?.kind === "group" && <GroupModal datasetId={datasetId} fields={all} existing={editing.field} onClose={() => setEditing(null)} />}
      {editing?.kind === "bin" && <BinModal datasetId={datasetId} fields={all} existing={editing.field} onClose={() => setEditing(null)} />}
      {hierarchy && <HierarchyModal datasetId={datasetId} fields={all} existing={hierarchy === "new" ? undefined : hierarchy} onClose={() => setHierarchy(null)} />}
      {parameter && <ParameterModal datasetId={datasetId} existing={parameter === "new" ? undefined : parameter} onClose={() => setParameter(null)} />}
    </div>
  );
}
