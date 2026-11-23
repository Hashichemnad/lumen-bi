import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Check, ChevronDown, Pencil, RotateCcw, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Input, Select } from "../../components/ui";
import { get } from "../../lib/api";
import type { DateUnit } from "../../lib/chart/types";
import { calendarDisplay, hijriDay } from "../../lib/dates";
import { describeValue, isActive } from "../../lib/dashboard/filters";
import { t } from "../../lib/i18n";
import type { Dashboard, DashFilter, DateValue, FilterValue } from "../../lib/dashboard/types";

const rel = (unit: DateUnit, n: number): DateValue => ({ mode: "relative", unit, n });
export const DATE_PRESETS: { label: string; value: DateValue | null }[] = [
  { label: "All dates", value: null },
  { label: "Today", value: rel("day", 1) },
  { label: "Last 7 days", value: rel("day", 7) },
  { label: "Last 30 days", value: rel("day", 30) },
  { label: "Last 90 days", value: rel("day", 90) },
  { label: "This week", value: rel("week", 1) },
  { label: "This month", value: rel("month", 1) },
  { label: "Last 3 months", value: rel("month", 3) },
  { label: "Last 12 months", value: rel("month", 12) },
  { label: "This quarter", value: rel("quarter", 1) },
  { label: "This year", value: rel("year", 1) },
  { label: "This fiscal quarter", value: rel("fiscal_quarter", 1) },
  { label: "This fiscal year", value: rel("fiscal_year", 1) },
  { label: "Last 2 fiscal years", value: rel("fiscal_year", 2) },
];

function useValues(dash: Dashboard, f: DashFilter, search: string, enabled: boolean) {
  const t = f.targets[0];
  return useQuery({
    queryKey: ["dash-values", dash.id, t?.dataset, t?.field, search],
    queryFn: () => get<{ values: unknown[]; truncated: boolean }>(`/dashboards/${dash.id}/values?dataset=${t.dataset}&field=${encodeURIComponent(t.field)}&search=${encodeURIComponent(search)}&limit=500`),
    enabled: enabled && !!t,
    placeholderData: (prev) => prev,
    staleTime: 60_000,
  });
}

const label = (v: unknown) => (v === null ? "(empty)" : String(v));

function MultiControl({ dash, f, value, onChange }: { dash: Dashboard; f: DashFilter; value: unknown[]; onChange: (v: unknown[]) => void }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const values = useValues(dash, f, search, open);
  const picked = new Set(value.map((v) => JSON.stringify(v)));
  const toggle = (v: unknown) => onChange(picked.has(JSON.stringify(v)) ? value.filter((x) => JSON.stringify(x) !== JSON.stringify(v)) : [...value, v]);
  return (
    <div className="relative">
      <button onClick={() => setOpen(!open)} aria-expanded={open}
        className={clsx("flex h-9 min-w-36 items-center gap-2 rounded-lg border bg-white px-3 text-sm", value.length ? "border-brand-300" : "border-slate-300")}>
        <span className="max-w-44 truncate">{describeValue(f, value)}</span>
        <ChevronDown size={14} className="ms-auto text-slate-400" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div className="absolute z-40 mt-1 w-72 rounded-lg border border-slate-200 bg-white p-2 shadow-lg">
            <Input autoFocus placeholder={t("Search")} value={search} onChange={(e) => setSearch(e.target.value)} className="py-1.5" />
            <div className="mt-2 max-h-64 overflow-y-auto">
              {values.data?.values.map((v) => (
                <label key={JSON.stringify(v)} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-slate-50">
                  <input type="checkbox" checked={picked.has(JSON.stringify(v))} onChange={() => toggle(v)} />
                  <span className="truncate">{label(v)}</span>
                </label>
              ))}
              {values.data && !values.data.values.length && <p className="p-2 text-sm text-slate-500">{t("No values")}</p>}
            </div>
            <div className="mt-2 flex justify-between border-t border-slate-100 pt-2 text-xs">
              <button className="text-slate-600 hover:underline" onClick={() => onChange([])}>{t("Clear")}</button>
              <button className="font-medium text-brand-700 hover:underline" onClick={() => setOpen(false)}>{t("Done")}</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function DropdownControl({ dash, f, value, onChange }: { dash: Dashboard; f: DashFilter; value: unknown[]; onChange: (v: unknown[]) => void }) {
  const values = useValues(dash, f, "", true);
  const current = value.length ? JSON.stringify(value[0]) : "";
  return (
    <div className="w-44">
      <Select className="h-9 py-1" value={current} onChange={(e) => onChange(e.target.value ? [JSON.parse(e.target.value)] : [])}>
        <option value="">{t("All")}</option>
        {values.data?.values.map((v) => <option key={JSON.stringify(v)} value={JSON.stringify(v)}>{label(v)}</option>)}
      </Select>
    </div>
  );
}

function SearchControl({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  useEffect(() => {
    const t = setTimeout(() => text !== value && onChange(text), 400);
    return () => clearTimeout(t);
  }, [text, value, onChange]);
  return <Input className="h-9 w-44 py-1" placeholder={t("Contains…")} value={text} onChange={(e) => setText(e.target.value)} />;
}

function DateControl({ value, onChange }: { value: DateValue | null; onChange: (v: DateValue | null) => void }) {
  const preset = DATE_PRESETS.findIndex((p) => JSON.stringify(p.value) === JSON.stringify(value));
  const custom = value?.mode === "range";
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-40">
        <Select className="h-9 py-1" value={custom ? "custom" : preset >= 0 ? String(preset) : "0"}
          onChange={(e) => onChange(e.target.value === "custom" ? { mode: "range", from: null, to: null } : DATE_PRESETS[Number(e.target.value)].value)}>
          {DATE_PRESETS.map((p, i) => <option key={p.label} value={i}>{t(p.label)}</option>)}
          <option value="custom">{t("Custom range…")}</option>
        </Select>
      </div>
      {custom && (
        <>
          <span className="flex flex-col">
            <Input type="date" className="h-9 w-36 py-1" value={value.from ?? ""} onChange={(e) => onChange({ ...value, from: e.target.value || null })} aria-label={t("From")} />
            {value.from && calendarDisplay() !== "gregorian" && <span className="mt-0.5 text-[11px] text-slate-500">{hijriDay(value.from)}</span>}
          </span>
          <span className="flex flex-col">
            <Input type="date" className="h-9 w-36 py-1" value={value.to ?? ""} onChange={(e) => onChange({ ...value, to: e.target.value || null })} aria-label={t("To")} />
            {value.to && calendarDisplay() !== "gregorian" && <span className="mt-0.5 text-[11px] text-slate-500">{hijriDay(value.to)}</span>}
          </span>
        </>
      )}
    </div>
  );
}

function RangeControl({ value, onChange }: { value: { min: number | null; max: number | null } | null; onChange: (v: { min: number | null; max: number | null }) => void }) {
  const v = value ?? { min: null, max: null };
  const num = (s: string) => (s === "" ? null : Number(s));
  return (
    <div className="flex items-center gap-1">
      <Input type="number" className="h-9 w-24 py-1" placeholder={t("Min")} value={v.min ?? ""} onChange={(e) => onChange({ ...v, min: num(e.target.value) })} aria-label={t("Minimum")} />
      <span className="text-slate-400">–</span>
      <Input type="number" className="h-9 w-24 py-1" placeholder={t("Max")} value={v.max ?? ""} onChange={(e) => onChange({ ...v, max: num(e.target.value) })} aria-label={t("Maximum")} />
    </div>
  );
}

export function FilterControl({ dash, f, value, onChange }: { dash: Dashboard; f: DashFilter; value: FilterValue; onChange: (v: FilterValue) => void }) {
  switch (f.control) {
    case "dropdown": return <DropdownControl dash={dash} f={f} value={(value as unknown[]) ?? []} onChange={onChange} />;
    case "search": return <SearchControl value={(value as string) ?? ""} onChange={onChange} />;
    case "date": return <DateControl value={(value as DateValue) ?? null} onChange={onChange} />;
    case "range": return <RangeControl value={value as { min: number | null; max: number | null }} onChange={onChange} />;
    default: return <MultiControl dash={dash} f={f} value={(value as unknown[]) ?? []} onChange={onChange} />;
  }
}

export default function FilterBar({ dash, filters, values, onChange, params, onParam, cross, onClearCross, onReset, editing, onEditFilter, onRemoveFilter }: {
  dash: Dashboard;
  filters: DashFilter[];
  values: Record<string, FilterValue>;
  onChange: (id: string, v: FilterValue) => void;
  params: Record<string, unknown>;
  onParam: (key: string, v: unknown) => void;
  cross: { label: string } | null;
  onClearCross: () => void;
  onReset: () => void;
  editing: boolean;
  onEditFilter: (f: DashFilter) => void;
  onRemoveFilter: (f: DashFilter) => void;
}) {
  const paramControls = Object.values(dash.datasets).flatMap((ds) => ds.parameters.map((p) => ({ ...p, key: `${ds.id}:${p.name}`, dataset: ds.name })));
  const changed = filters.some((f) => JSON.stringify(values[f.id] ?? null) !== JSON.stringify(f.value ?? null)) || Object.keys(params).length > 0 || !!cross;
  if (!filters.length && !paramControls.length && !cross) return null;
  return (
    <div role="group" aria-label={t("Filters")} className="mb-5 flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      {filters.map((f) => (
        <div key={f.id}>
          <div className="mb-1 flex items-center gap-1 text-xs font-medium text-slate-500">
            {t(f.label)}
            {isActive(f, values[f.id] ?? null) && <Check size={12} className="text-brand-600" />}
            {editing && (
              <>
                <button onClick={() => onEditFilter(f)} className="rounded p-0.5 text-slate-400 hover:text-slate-700" aria-label={t("Edit filter {label}", { label: f.label })}><Pencil size={11} /></button>
                <button onClick={() => onRemoveFilter(f)} className="rounded p-0.5 text-slate-400 hover:text-red-600" aria-label={t("Remove filter {label}", { label: f.label })}><X size={11} /></button>
              </>
            )}
          </div>
          <FilterControl dash={dash} f={f} value={values[f.id] ?? null} onChange={(v) => onChange(f.id, v)} />
        </div>
      ))}
      {paramControls.map((p) => {
        const value = params[p.key] ?? p.default_value;
        return (
          <div key={p.key}>
            <div className="mb-1 text-xs font-medium text-slate-600" title={p.description || p.dataset}>{p.name}</div>
            {p.allowed_values.length ? (
              <div className="w-40">
                <Select className="h-9 py-1" value={String(value ?? "")} onChange={(e) => onParam(p.key, e.target.value)}>
                  {p.allowed_values.map((v) => <option key={String(v)} value={String(v)}>{String(v)}</option>)}
                </Select>
              </div>
            ) : (
              <Input className="h-9 w-32 py-1" type={["number", "integer"].includes(p.data_type) ? "number" : p.data_type === "date" ? "date" : "text"}
                value={String(value ?? "")} onChange={(e) => onParam(p.key, ["number", "integer"].includes(p.data_type) ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)} />
            )}
          </div>
        );
      })}
      {cross && (
        <span className="mb-1 inline-flex h-7 items-center gap-1 rounded-full bg-brand-50 px-3 text-xs text-brand-800">
          {t("Filtered by")} {t(cross.label)}
          <button onClick={onClearCross} aria-label={t("Clear the chart filter")}><X size={12} /></button>
        </span>
      )}
      {changed && (
        <button onClick={onReset} className="ms-auto inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-semibold text-brand-700 hover:bg-brand-50"><RotateCcw size={14} /> {t("Reset")}</button>
      )}
    </div>
  );
}
