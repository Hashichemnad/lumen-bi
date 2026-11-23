import { Calculator, Calendar, Hash, Layers, Plus, Search, SlidersHorizontal, Type } from "lucide-react";
import { useState } from "react";

import { Input, Select } from "../../components/ui";
import type { Catalog } from "../../lib/chart/types";
import type { Field } from "../../lib/types";
import { readDrag, setDrag, type DragPayload } from "./Shelf";

function icon(f: Field) {
  if (f.kind === "calculated") return <Calculator size={13} />;
  if (f.data_type === "date" || f.data_type === "datetime") return <Calendar size={13} />;
  if (f.data_type === "integer" || f.data_type === "number") return <Hash size={13} />;
  return <Type size={13} />;
}

function FieldItem({ f, role, onAdd }: { f: Field | null; role: "dimension" | "measure"; onAdd: () => void }) {
  const name = f ? f.label || f.name : "Number of rows";
  return (
    <div draggable onDragStart={(e) => setDrag(e, { from: "field", field: f ? f.name : "*", role })} onDoubleClick={onAdd}
      title={f?.description || (f ? f.name : "Counts the rows")}
      className="group flex cursor-grab items-center gap-1.5 rounded px-2 py-1 text-sm hover:bg-slate-100 active:cursor-grabbing">
      <span className={role === "measure" ? "text-emerald-600" : "text-sky-600"}>{f ? icon(f) : <Hash size={13} />}</span>
      <span className="min-w-0 flex-1 truncate">{name}</span>
      <button onClick={onAdd} className="rounded p-0.5 text-slate-400 opacity-0 hover:bg-slate-200 hover:text-slate-700 group-hover:opacity-100" aria-label={`Add ${name}`}>
        <Plus size={13} />
      </button>
    </div>
  );
}

export default function FieldPanel({ catalog, params, onParam, onAdd, onRemovePill }: {
  catalog: Catalog;
  params: Record<string, unknown>;
  onParam: (name: string, value: unknown) => void;
  onAdd: (field: Field | "*", role?: "dimension" | "measure") => void;
  onRemovePill: (id: string) => void;
}) {
  const [q, setQ] = useState("");
  const match = (f: Field) => !q || `${f.label} ${f.name}`.toLowerCase().includes(q.toLowerCase());
  const usable = catalog.fields.filter((f) => !f.hidden && !f.missing && !f.error && match(f));
  const inHierarchy = new Set(catalog.hierarchies.flatMap((h) => h.levels));
  const dims = usable.filter((f) => !f.is_aggregate && f.role === "dimension" && !inHierarchy.has(f.name));
  const measures = usable.filter((f) => f.is_aggregate || f.role === "measure");
  const byName = new Map(catalog.fields.map((f) => [f.name, f]));
  const drop = (e: React.DragEvent) => {
    e.preventDefault();
    const p = readDrag(e) as DragPayload | null;
    if (p?.from === "pill") onRemovePill(p.id); // drag a pill back to the field list to remove it
  };
  return (
    <aside onDragOver={(e) => e.preventDefault()} onDrop={drop} className="flex min-h-0 flex-col border-r border-slate-200 bg-white">
      <div className="border-b border-slate-200 p-3">
        <div className="truncate text-sm font-semibold" title={catalog.name}>{catalog.name}</div>
        <div className="relative mt-2">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search fields" className="py-1.5 pl-8" />
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        <p className="px-2 pb-1 pt-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Dimensions</p>
        {catalog.hierarchies.map((h) => {
          const levels = h.levels.map((l) => byName.get(l)).filter((f): f is Field => !!f && match(f) && !f.hidden);
          if (!levels.length) return null;
          return (
            <div key={h.id} className="mb-1">
              <div className="flex items-center gap-1.5 px-2 py-1 text-xs font-medium text-slate-500"><Layers size={13} /> {h.name}</div>
              <div className="ml-3 border-l border-slate-200 pl-1">
                {levels.map((f) => <FieldItem key={f.name} f={f} role="dimension" onAdd={() => onAdd(f, "dimension")} />)}
              </div>
            </div>
          );
        })}
        {dims.map((f) => <FieldItem key={f.name} f={f} role="dimension" onAdd={() => onAdd(f)} />)}
        <p className="px-2 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Measures</p>
        {measures.map((f) => <FieldItem key={f.name} f={f} role="measure" onAdd={() => onAdd(f)} />)}
        {!q && <FieldItem f={null} role="measure" onAdd={() => onAdd("*")} />}
        {catalog.parameters.length > 0 && (
          <>
            <p className="flex items-center gap-1 px-2 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-400"><SlidersHorizontal size={12} /> Parameters</p>
            {catalog.parameters.map((p) => {
              const value = params[p.name] ?? p.default_value;
              return (
                <label key={p.id} className="block px-2 py-1 text-sm">
                  <span className="mb-1 block text-xs text-slate-600">{p.name}</span>
                  {p.allowed_values.length ? (
                    <Select className="py-1" value={String(value ?? "")} onChange={(e) => onParam(p.name, e.target.value)}>
                      {p.allowed_values.map((v) => <option key={String(v)} value={String(v)}>{String(v)}</option>)}
                    </Select>
                  ) : p.data_type === "boolean" ? (
                    <input type="checkbox" checked={!!value} onChange={(e) => onParam(p.name, e.target.checked)} />
                  ) : (
                    <Input className="py-1" type={p.data_type === "date" ? "date" : ["number", "integer"].includes(p.data_type) ? "number" : "text"}
                      value={String(value ?? "")}
                      onChange={(e) => onParam(p.name, ["number", "integer"].includes(p.data_type) ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)} />
                  )}
                </label>
              );
            })}
          </>
        )}
      </div>
      <p className="border-t border-slate-200 px-3 py-2 text-[11px] text-slate-400">Drag fields to the shelves, or double-click.</p>
    </aside>
  );
}
