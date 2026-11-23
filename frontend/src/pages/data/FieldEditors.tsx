import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Plus, Trash2, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Alert, Badge, Button, Field as FormField, Input, Modal, Select } from "../../components/ui";
import { get, patch, post } from "../../lib/api";
import type { Field, FormulaCheck, GroupConfig, Parameter } from "../../lib/types";
import { FUNCTION_GROUPS } from "./functions";
import { t } from "../../lib/i18n";

export const TYPE_LABELS: Record<string, string> = {
  integer: "Whole number", number: "Number", string: "Text", boolean: "Yes/No", date: "Date", datetime: "Date & time",
  other: "Other", null: "Empty",
};

function invalidate(qc: ReturnType<typeof useQueryClient>, datasetId: number) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: ["fields", datasetId] }),
    qc.invalidateQueries({ queryKey: ["preview", datasetId] }),
  ]);
}

function sampleText(v: unknown): string {
  if (v === null || v === undefined) return "(empty)";
  if (typeof v === "number") return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return String(v);
}

function CheckResult({ result, formula }: { result: FormulaCheck | null; formula: string }) {
  if (!result) return null;
  if (!result.ok) {
    const pos = result.pos ?? -1;
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
        <div className="flex items-start gap-2"><XCircle size={18} className="shrink-0" /> {result.error}</div>
        {pos >= 0 && pos <= formula.length && (
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap font-mono text-xs text-slate-700">
            {formula.slice(0, pos)}<mark className="rounded bg-red-200 px-0.5">{formula.slice(pos, pos + 1) || " "}</mark>{formula.slice(pos + 1)}
          </pre>
        )}
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
      <div className="flex flex-wrap items-center gap-2">
        <CheckCircle2 size={18} /> {t("Valid ·")} {t(TYPE_LABELS[result.type ?? ""] ?? result.type)}
        {result.is_aggregate && <Badge tone="blue">{t("aggregate")}</Badge>}
      </div>
      {result.sample.length > 0 && (
        <div className="mt-1 text-xs text-emerald-900">
          {result.is_aggregate ? t("Result on all data: ") : t("First values: ")}
          {result.sample.slice(0, 6).map(sampleText).join(" · ")}
        </div>
      )}
    </div>
  );
}

// ===================================================================== calculated field
export function CalculatedFieldModal({ datasetId, fields, params, existing, onClose }: {
  datasetId: number;
  fields: Field[];
  params: Parameter[];
  existing?: Field;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState(existing?.name ?? "");
  const [formula, setFormula] = useState(existing?.formula ?? "");
  const [result, setResult] = useState<FormulaCheck | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);

  // Check as you type (debounced)
  useEffect(() => {
    if (!formula.trim()) {
      setResult(null);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        setResult(await post<FormulaCheck>(`/datasets/${datasetId}/formula/check`,
          { formula, field_name: existing?.name ?? (name || null) }));
      } catch {
        setResult(null);
      }
    }, 500);
    return () => clearTimeout(timer);
  }, [formula, datasetId, existing?.name, name]);

  const insert = (text: string) => {
    const el = area.current;
    if (!el) return setFormula((f) => f + text);
    const start = el.selectionStart, end = el.selectionEnd;
    const next = formula.slice(0, start) + text + formula.slice(end);
    setFormula(next);
    requestAnimationFrame(() => {
      el.focus();
      el.selectionStart = el.selectionEnd = start + text.length;
    });
  };

  const save = async () => {
    setError("");
    try {
      if (existing) await patch(`/datasets/${datasetId}/fields/${existing.id}`, { name, formula });
      else await post(`/datasets/${datasetId}/fields`, { name, formula, kind: "calculated" });
      await invalidate(qc, datasetId);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save"));
    }
  };

  const visible = fields.filter((f) => !f.missing && f.name !== existing?.name &&
    `${f.name} ${f.label}`.toLowerCase().includes(filter.toLowerCase()));

  return (
    <Modal open wide title={existing ? `Edit ${existing.name}` : t("New calculated field")} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button>
        <Button onClick={save} disabled={!name.trim() || !formula.trim()}>{t("Save")}</Button></>}>
      {error && <Alert>{error}</Alert>}
      <FormField label={t("Name")}><Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("Profit")} autoFocus /></FormField>
      <div className="grid gap-4 md:grid-cols-[1fr_220px]">
        <div className="space-y-3">
          <FormField label={t("Formula")} hint={t("Fields go in [brackets], parameters start with @. Click a field or function to insert it.")}>
            <textarea ref={area} spellCheck={false}
              className="block h-40 w-full rounded-lg border border-slate-300 p-3 font-mono text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
              placeholder={t("[Sales] - [Cost]")} value={formula} onChange={(e) => setFormula(e.target.value)} />
          </FormField>
          <CheckResult result={result} formula={formula} />
          <details className="rounded-lg border border-slate-200 p-3 text-sm">
            <summary className="cursor-pointer font-medium text-slate-700">{t("Functions")}</summary>
            <div className="mt-2 grid gap-3 sm:grid-cols-2">
              {FUNCTION_GROUPS.map((g) => (
                <div key={g.name}>
                  <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{g.name}</div>
                  <ul className="space-y-0.5">
                    {g.items.map(([sig, help]) => (
                      <li key={sig}>
                        <button type="button" className="text-start hover:text-brand-700"
                          onClick={() => insert(sig.match(/^[A-Z]+\(/) ? sig.split("(")[0] + "(" : sig.split(" / ")[0])}>
                          <code className="text-xs">{sig}</code> <span className="text-xs text-slate-500">{help}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </details>
        </div>
        <div className="text-sm">
          <Input placeholder={t("Find a field")} value={filter} onChange={(e) => setFilter(e.target.value)} className="mb-2" />
          <div className="max-h-72 space-y-0.5 overflow-y-auto rounded-lg border border-slate-200 p-1">
            {visible.map((f) => (
              <button key={f.id} type="button" onClick={() => insert(`[${f.name.replace(/]/g, "]]")}]`)}
                className="flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-start hover:bg-slate-50">
                <span className="truncate">{f.label || f.name}</span>
                <span className="shrink-0 text-[10px] uppercase text-slate-400">{t(TYPE_LABELS[f.data_type] ?? f.data_type)}</span>
              </button>
            ))}
            {params.map((p) => (
              <button key={`p${p.id}`} type="button" onClick={() => insert(/\s/.test(p.name) ? `@[${p.name}]` : `@${p.name}`)}
                className="flex w-full items-center justify-between rounded px-2 py-1 text-start hover:bg-slate-50">
                <span className="truncate text-brand-700">@{p.name}</span>
                <span className="text-[10px] uppercase text-slate-400">{t("parameter")}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}

// ===================================================================== group
export function GroupModal({ datasetId, fields, existing, onClose }: {
  datasetId: number;
  fields: Field[];
  existing?: Field;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const cfg = (existing?.config ?? {}) as Partial<GroupConfig>;
  const candidates = fields.filter((f) => !f.missing && f.role === "dimension" && !f.is_aggregate && f.kind !== "group");
  const [name, setName] = useState(existing?.name ?? "");
  const [source, setSource] = useState(cfg.source ?? candidates[0]?.name ?? "");
  const [groups, setGroups] = useState<{ name: string; values: (string | number)[] }[]>(cfg.groups ?? [{ name: "", values: [] }]);
  const [other, setOther] = useState(cfg.other ?? "Other");
  const [error, setError] = useState("");
  const values = useQuery({
    queryKey: ["values", datasetId, source],
    queryFn: () => get<{ values: (string | number | null)[]; truncated: boolean }>(`/datasets/${datasetId}/values?field=${encodeURIComponent(source)}`),
    enabled: !!source,
  });
  const taken = new Map(groups.flatMap((g, gi) => g.values.map((v) => [String(v), gi] as const)));
  const toggle = (gi: number, v: string | number) => setGroups(groups.map((g, i) => {
    if (i !== gi) return { ...g, values: g.values.filter((x) => String(x) !== String(v)) };
    const has = g.values.some((x) => String(x) === String(v));
    return { ...g, values: has ? g.values.filter((x) => String(x) !== String(v)) : [...g.values, v] };
  }));

  const save = async () => {
    setError("");
    const config = { source, groups: groups.filter((g) => g.name.trim() && g.values.length), other };
    try {
      if (existing) await patch(`/datasets/${datasetId}/fields/${existing.id}`, { name, config });
      else await post(`/datasets/${datasetId}/fields`, { name, kind: "group", config });
      await invalidate(qc, datasetId);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save"));
    }
  };

  return (
    <Modal open wide title={existing ? `Edit ${existing.name}` : t("New group")} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button><Button onClick={save} disabled={!name.trim()}>{t("Save")}</Button></>}>
      {error && <Alert>{error}</Alert>}
      <p className="text-sm text-slate-600">{t("Put values into named groups, e.g. KSA, QAT and OMN into \"GCC\".")}</p>
      <div className="grid gap-4 sm:grid-cols-3">
        <FormField label={t("Name")}><Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("Market")} /></FormField>
        <FormField label={t("Values from")}>
          <Select value={source} onChange={(e) => { setSource(e.target.value); setGroups([{ name: "", values: [] }]); }}>
            {candidates.map((f) => <option key={f.id} value={f.name}>{f.label || f.name}</option>)}
          </Select>
        </FormField>
        <FormField label={t("Everything else")} hint={t("Empty = keep the original value.")}>
          <Input value={other} onChange={(e) => setOther(e.target.value)} />
        </FormField>
      </div>
      <div className="space-y-3">
        {groups.map((g, gi) => (
          <div key={gi} className="rounded-lg border border-slate-200 p-3">
            <div className="mb-2 flex gap-2">
              <Input placeholder={t("Group {v0} name", { v0: gi + 1 })} value={g.name}
                onChange={(e) => setGroups(groups.map((x, i) => (i === gi ? { ...x, name: e.target.value } : x)))} />
              <Button type="button" variant="ghost" aria-label={t("Remove group")} onClick={() => setGroups(groups.filter((_, i) => i !== gi))}><Trash2 size={16} /></Button>
            </div>
            <div className="flex max-h-32 flex-wrap gap-1.5 overflow-y-auto">
              {values.data?.values.filter((v) => v !== null).map((v) => {
                const owner = taken.get(String(v));
                const mine = owner === gi;
                return (
                  <button key={String(v)} type="button" onClick={() => toggle(gi, v as string | number)}
                    className={`rounded-full border px-2.5 py-0.5 text-xs ${mine ? "border-brand-500 bg-brand-50 text-brand-700" : owner !== undefined ? "border-slate-200 text-slate-300" : "border-slate-300 text-slate-700 hover:bg-slate-50"}`}>
                    {String(v)}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
        <Button type="button" variant="secondary" size="sm" onClick={() => setGroups([...groups, { name: "", values: [] }])}><Plus size={14} /> {t("Add group")}</Button>
        {values.data?.truncated && <p className="text-xs text-slate-500">{t("Showing the 200 most common values.")}</p>}
      </div>
    </Modal>
  );
}

// ===================================================================== bins
export function BinModal({ datasetId, fields, existing, onClose }: {
  datasetId: number;
  fields: Field[];
  existing?: Field;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const numeric = fields.filter((f) => !f.missing && !f.is_aggregate && ["integer", "number"].includes(f.data_type));
  const [name, setName] = useState(existing?.name ?? "");
  const [source, setSource] = useState(String(existing?.config.source ?? numeric[0]?.name ?? ""));
  const [size, setSize] = useState(String(existing?.config.size ?? 10));
  const [error, setError] = useState("");
  const save = async () => {
    setError("");
    const config = { source, size: Number(size) };
    try {
      if (existing) await patch(`/datasets/${datasetId}/fields/${existing.id}`, { name, config });
      else await post(`/datasets/${datasetId}/fields`, { name, kind: "bin", config });
      await invalidate(qc, datasetId);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save"));
    }
  };
  return (
    <Modal open title={existing ? `Edit ${existing.name}` : t("New bins")} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button><Button onClick={save} disabled={!name.trim()}>{t("Save")}</Button></>}>
      {error && <Alert>{error}</Alert>}
      <p className="text-sm text-slate-600">{t("Turn a number into ranges, e.g. order values in steps of 1,000 (0, 1000, 2000…).")}</p>
      <FormField label={t("Name")}><Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("Order size band")} /></FormField>
      <FormField label={t("Number field")}>
        <Select value={source} onChange={(e) => setSource(e.target.value)}>
          {numeric.map((f) => <option key={f.id} value={f.name}>{f.label || f.name}</option>)}
        </Select>
      </FormField>
      <FormField label={t("Size of each range")}><Input type="number" min="0" step="any" value={size} onChange={(e) => setSize(e.target.value)} /></FormField>
    </Modal>
  );
}
