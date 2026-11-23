import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";

import { t } from "../../lib/i18n";
import { useMemo, useState } from "react";

import { Button, Field as FormField, Input, Modal, Select } from "../../components/ui";
import { formatValue } from "../../lib/chart/format";
import type { Agg, Catalog, FilterKind, FilterPill } from "../../lib/chart/types";
import { newId } from "../../lib/chart/types";
import { get } from "../../lib/api";
import { AGG_LABEL, isDate, isNumber } from "./pills";

interface Values {
  values: unknown[];
  counts: number[];
  truncated: boolean;
}

const KIND_LABEL: Record<FilterKind, string> = {
  values: "Pick values", range: "Range", relative: "Relative dates", top: "Top / bottom", text: "Text match",
};

export default function FilterModal({ catalog, field, existing, onSave, onClose }: {
  catalog: Catalog;
  field: string;
  existing?: FilterPill;
  onSave: (f: FilterPill) => void;
  onClose: () => void;
}) {
  const f = catalog.fields.find((x) => x.name === field);
  const date = isDate(f);
  const number = isNumber(f);
  const measure = !!f && (f.role === "measure" || f.is_aggregate);
  const kinds: FilterKind[] = f?.is_aggregate
    ? ["range"]
    : date
      ? ["relative", "range", "values"]
      : number
        ? measure ? ["range", "values"] : ["values", "range"]
        : ["values", "text", "top"];
  const [draft, setDraft] = useState<FilterPill>(
    existing ?? { id: newId("f"), field, kind: kinds[0], values: [], unit: "month", n: date ? 12 : 10, top: "top", op: "contains", ...(measure && !f?.is_aggregate ? { agg: "sum" as Agg } : {}) },
  );
  const [search, setSearch] = useState("");
  const set = (changes: Partial<FilterPill>) => setDraft({ ...draft, ...changes });
  const values = useQuery({
    queryKey: ["values", catalog.id, field, search],
    queryFn: () => get<Values>(`/datasets/${catalog.id}/values?field=${encodeURIComponent(field)}&order=value&limit=500&search=${encodeURIComponent(search)}`),
    enabled: draft.kind === "values",
    placeholderData: (prev) => prev,
  });
  const selected = useMemo(() => new Set((draft.values ?? []).map((v) => JSON.stringify(v))), [draft.values]);
  const toggle = (v: unknown) => {
    const key = JSON.stringify(v);
    set({ values: selected.has(key) ? (draft.values ?? []).filter((x) => JSON.stringify(x) !== key) : [...(draft.values ?? []), v] });
  };
  const measures = catalog.fields.filter((x) => !x.hidden && (x.role === "measure" || x.is_aggregate));
  const col = f ? { type: f.data_type, format: f.format, grain: null, part: null } : undefined;
  const label = f?.label || field;

  return (
    <Modal open wide title={t("Filter {label}", { label })} onClose={onClose}
      footer={<>
        <Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button>
        <Button onClick={() => { onSave(draft); onClose(); }}>{t("Apply")}</Button>
      </>}>
      {kinds.length > 1 && (
        <div className="flex gap-1 rounded-lg bg-slate-100 p-1">
          {kinds.map((k) => (
            <button key={k} onClick={() => set({ kind: k })}
              className={clsx("flex-1 rounded-md px-3 py-1.5 text-sm", draft.kind === k ? "bg-white font-medium shadow-sm" : "text-slate-600")}>{t(KIND_LABEL[k])}</button>
          ))}
        </div>
      )}

      {draft.kind === "values" && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Input placeholder={t("Search values")} value={search} onChange={(e) => setSearch(e.target.value)} />
            <Button variant="secondary" className="shrink-0" onClick={() => set({ values: [...new Set([...(draft.values ?? []), ...(values.data?.values ?? [])])] })}>{t("All")}</Button>
            <Button variant="secondary" className="shrink-0" onClick={() => set({ values: [] })}>{t("None")}</Button>
          </div>
          <div className="max-h-72 overflow-y-auto rounded-lg border border-slate-200">
            {values.data?.values.map((v, i) => (
              <label key={JSON.stringify(v)} className="flex cursor-pointer items-center gap-2 border-b border-slate-100 px-3 py-1.5 text-sm last:border-0 hover:bg-slate-50">
                <input type="checkbox" checked={selected.has(JSON.stringify(v))} onChange={() => toggle(v)} />
                <span className="flex-1">{v === null ? <span className="text-slate-400">{t("(empty)")}</span> : formatValue(v, col)}</span>
                <span className="text-xs tabular-nums text-slate-400">{values.data?.counts[i]?.toLocaleString()}</span>
              </label>
            ))}
            {values.data && !values.data.values.length && <p className="p-3 text-sm text-slate-500">{t("No values")}</p>}
          </div>
          {values.data?.truncated && <p className="text-xs text-slate-500">{t("Showing the first 500 values; search to find others.")}</p>}
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={!!draft.exclude} onChange={(e) => set({ exclude: e.target.checked })} />
            {t("Exclude the selected values instead")}
          </label>
          <p className="text-xs text-slate-500">{t("{length} selected", { length: (draft.values ?? []).length })}</p>
        </div>
      )}

      {draft.kind === "range" && (
        <div className="space-y-3">
          {measure && !f?.is_aggregate && (
            <FormField label={t("Compare")}>
              <Select value={draft.agg ?? ""} onChange={(e) => set({ agg: (e.target.value || undefined) as Agg | undefined })}>
                <option value="">{t("Each row's value")}</option>
                {(["sum", "avg", "min", "max", "count"] as Agg[]).map((a) => <option key={a} value={a}>{t("{AGG_LABEL} per mark (after grouping)", { AGG_LABEL: AGG_LABEL[a] })}</option>)}
              </Select>
            </FormField>
          )}
          <div className="grid grid-cols-2 gap-3">
            <FormField label={t("From (optional)")}>
              <Input type={date ? "date" : "number"} value={(draft.min as string | number | undefined) ?? ""} onChange={(e) => set({ min: e.target.value === "" ? null : date ? e.target.value : Number(e.target.value) })} />
            </FormField>
            <FormField label={t("To (optional)")}>
              <Input type={date ? "date" : "number"} value={(draft.max as string | number | undefined) ?? ""} onChange={(e) => set({ max: e.target.value === "" ? null : date ? e.target.value : Number(e.target.value) })} />
            </FormField>
          </div>
        </div>
      )}

      {draft.kind === "relative" && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-1.5">
            {([[1, "day", "Today"], [7, "day", "Last 7 days"], [30, "day", "Last 30 days"], [1, "month", "This month"], [3, "month", "Last 3 months"], [12, "month", "Last 12 months"], [1, "year", "This year"], [1, "fiscal_year", "This fiscal year"]] as const).map(([n, unit, text]) => (
              <button key={text} onClick={() => set({ n, unit })}
                className={clsx("rounded-full border px-3 py-1 text-sm", draft.n === n && draft.unit === unit ? "border-brand-600 bg-brand-50 text-brand-700" : "border-slate-200 hover:bg-slate-50")}>{text}</button>
            ))}
          </div>
          <div className="flex items-end gap-2">
            <FormField label={t("Last")}>
              <Input type="number" min={1} className="w-24" value={draft.n ?? 1} onChange={(e) => set({ n: Math.max(1, Number(e.target.value) || 1) })} />
            </FormField>
            <div className="w-40">
              <Select value={draft.unit} onChange={(e) => set({ unit: e.target.value as FilterPill["unit"] })}>
                {(["day", "week", "month", "quarter", "year", "fiscal_quarter", "fiscal_year"] as const).map((u) => <option key={u} value={u}>{t({ day: "days", week: "weeks", month: "months", quarter: "quarters", year: "years", fiscal_quarter: "fiscal quarters", fiscal_year: "fiscal years" }[u])}</option>)}
              </Select>
            </div>
          </div>
          <p className="text-xs text-slate-500">{t("Includes the current period, up to today.")}</p>
        </div>
      )}

      {draft.kind === "top" && (
        <div className="grid grid-cols-[auto_6rem_1fr] items-end gap-2">
          <Select value={draft.top} onChange={(e) => set({ top: e.target.value as "top" | "bottom" })}>
            <option value="top">{t("Top")}</option>
            <option value="bottom">{t("Bottom")}</option>
          </Select>
          <Input type="number" min={1} value={draft.n ?? 10} onChange={(e) => set({ n: Math.max(1, Number(e.target.value) || 1) })} />
          <FormField label={t("{label} by", { label })}>
            <Select value={draft.by?.field ?? ""} onChange={(e) => set({ by: e.target.value ? { field: e.target.value } : undefined })}>
              <option value="">{t("Choose a measure")}</option>
              <option value="*">{t("Number of rows")}</option>
              {measures.map((m) => <option key={m.name} value={m.name}>{m.label || m.name}</option>)}
            </Select>
          </FormField>
          <p className="col-span-3 text-xs text-slate-500">{t("Ranked after the other filters.")}</p>
        </div>
      )}

      {draft.kind === "text" && (
        <div className="grid grid-cols-[12rem_1fr] gap-2">
          <Select value={draft.op} onChange={(e) => set({ op: e.target.value as FilterPill["op"] })}>
            <option value="contains">{t("Contains")}</option>
            <option value="not_contains">{t("Does not contain")}</option>
            <option value="starts_with">{t("Starts with")}</option>
            <option value="ends_with">{t("Ends with")}</option>
          </Select>
          <Input value={draft.text ?? ""} onChange={(e) => set({ text: e.target.value })} placeholder={t("Text (any case)")} />
        </div>
      )}
    </Modal>
  );
}
