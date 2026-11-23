import clsx from "clsx";
import { Check, ChevronDown, X } from "lucide-react";
import { useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";

import type { Agg, CalcType, Catalog, Grain, Part, Pill, ShelfName } from "../../lib/chart/types";
import type { Field } from "../../lib/types";
import { AGG_LABEL, CALC_LABEL, GRAIN_LABEL, PART_LABEL, drillDown, drillUp, isDate, isNumber, pillText } from "./pills";
import { t } from "../../lib/i18n";

export const DND = "application/x-lumen";
export type DragPayload = { from: "field"; field: string; role: "dimension" | "measure" } | { from: "pill"; id: string };

export function setDrag(e: DragEvent, payload: DragPayload) {
  e.dataTransfer.setData(DND, JSON.stringify(payload));
  e.dataTransfer.effectAllowed = "copyMove";
}

export function readDrag(e: DragEvent): DragPayload | null {
  try {
    return JSON.parse(e.dataTransfer.getData(DND)) as DragPayload;
  } catch {
    return null;
  }
}

// ------------------------------------------------------------------ popover menu
export function Menu({ anchor, onClose, children }: { anchor: { x: number; y: number }; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && onClose();
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    setTimeout(() => window.addEventListener("mousedown", close), 0);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", key);
    };
  }, [onClose]);
  const left = Math.min(anchor.x, window.innerWidth - 240);
  const top = Math.min(anchor.y, window.innerHeight - 380);
  return (
    <div ref={ref} role="menu" style={{ left, top }}
      className="fixed z-50 max-h-[70vh] w-56 overflow-y-auto rounded-lg border border-slate-200 bg-white py-1 text-sm shadow-lg">
      {children}
    </div>
  );
}

export function MenuItem({ onClick, children, checked, disabled }: { onClick: () => void; children: ReactNode; checked?: boolean; disabled?: boolean }) {
  return (
    <button role="menuitem" disabled={disabled} onClick={onClick}
      className="flex w-full items-center gap-2 px-3 py-1.5 text-start hover:bg-slate-100 disabled:opacity-40">
      <span className="w-4">{checked && <Check size={14} className="text-brand-600" />}</span>
      {children}
    </button>
  );
}

export const MenuTitle = ({ children }: { children: ReactNode }) => (
  <div className="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{children}</div>
);
export const MenuLine = () => <div className="my-1 border-t border-slate-100" />;

// ------------------------------------------------------------------ pills
export interface PillActions {
  change: (id: string, next: Pill | null) => void;
  drill: (id: string, dir: "down" | "up") => void;
  sort: (id: string, dir: "asc" | "desc" | null) => void;
  filter: (field: string) => void;
}

function PillChip({ pill, fields, catalog, sortDir, actions }: {
  pill: Pill; fields: Map<string, Field>; catalog?: Catalog; sortDir: "asc" | "desc" | null; actions: PillActions;
}) {
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const f = fields.get(pill.field);
  const close = () => setMenu(null);
  const set = (changes: Partial<Pill>) => {
    actions.change(pill.id, { ...pill, ...changes });
    close();
  };
  const down = drillDown(pill, catalog);
  const up = drillUp(pill, catalog);
  const measure = pill.role === "measure";
  const missing = pill.field !== "*" && !f;
  return (
    <>
      <span draggable onDragStart={(e) => setDrag(e, { from: "pill", id: pill.id })}
        className={clsx(
          "inline-flex cursor-grab items-center gap-1 rounded-full border py-0.5 ps-2.5 pe-1 text-xs font-medium active:cursor-grabbing",
          missing ? "border-red-300 bg-red-50 text-red-700" : measure ? "pill-measure border-emerald-200 bg-emerald-50 text-emerald-800" : "pill-dim border-sky-200 bg-sky-50 text-sky-800",
        )}
        title={missing ? t("This field no longer exists") : undefined}>
        <button onClick={(e) => setMenu({ x: e.clientX, y: e.clientY + 8 })} className="inline-flex items-center gap-0.5" aria-label={t("Options for {v0}", { v0: pillText(pill, fields) })}>
          {pillText(pill, fields)}
          {sortDir && <span className="text-[10px]">{sortDir === "asc" ? "↑" : "↓"}</span>}
          <ChevronDown size={12} />
        </button>
        <button onClick={() => actions.change(pill.id, null)} className="rounded-full p-0.5 hover:bg-black/5" aria-label={t("Remove")}><X size={12} /></button>
      </span>
      {menu && (
        <Menu anchor={menu} onClose={close}>
          {measure && !f?.is_aggregate && pill.field !== "*" && (
            <>
              <MenuTitle>{t("Total")}</MenuTitle>
              {(Object.keys(AGG_LABEL) as Agg[])
                .filter((a) => isNumber(f) || ["count", "count_distinct", "min", "max"].includes(a))
                .filter((a) => a !== "var")
                .map((a) => <MenuItem key={a} checked={(pill.agg ?? f?.default_agg) === a} onClick={() => set({ agg: a, p: a === "percentile" ? 0.9 : undefined })}>{t(AGG_LABEL[a])}</MenuItem>)}
              <MenuLine />
            </>
          )}
          {measure && (
            <>
              <MenuTitle>{t("Quick calculation")}</MenuTitle>
              <MenuItem checked={!pill.calc} onClick={() => set({ calc: undefined })}>{t("None")}</MenuItem>
              {(Object.keys(CALC_LABEL) as CalcType[]).map((c) => (
                <MenuItem key={c} checked={pill.calc?.type === c} onClick={() => set({ calc: { type: c, ...(c === "moving_avg" ? { window: 3 } : {}) } })}>{t(CALC_LABEL[c])}</MenuItem>
              ))}
              <MenuLine />
            </>
          )}
          {!measure && isDate(f) && (
            <>
              <MenuTitle>{t("Date")}</MenuTitle>
              {(["year", "quarter", "month", "week", "day", "fiscal_year", "fiscal_quarter"] as Grain[]).map((g) => <MenuItem key={g} checked={pill.grain === g} onClick={() => set({ grain: g, part: undefined })}>{t(GRAIN_LABEL[g])}</MenuItem>)}
              {f?.data_type === "datetime" && <MenuItem checked={pill.grain === "hour"} onClick={() => set({ grain: "hour", part: undefined })}>{t("Hour")}</MenuItem>}
              <MenuItem checked={!pill.grain && !pill.part} onClick={() => set({ grain: undefined, part: undefined })}>{t("Exact date")}</MenuItem>
              <MenuTitle>{t("Date part")}</MenuTitle>
              {(["month", "quarter", "weekday", "day", "year", "fiscal_quarter", "fiscal_year"] as Part[]).map((p) => <MenuItem key={p} checked={pill.part === p} onClick={() => set({ part: p, grain: undefined })}>{t(PART_LABEL[p])}</MenuItem>)}
              <MenuLine />
            </>
          )}
          {(down || up) && (
            <>
              {down && <MenuItem onClick={() => { actions.drill(pill.id, "down"); close(); }}>{t("Drill down to {label}", { label: down.label })}</MenuItem>}
              {up && <MenuItem onClick={() => { actions.drill(pill.id, "up"); close(); }}>{t("Drill up to {label}", { label: up.label })}</MenuItem>}
              <MenuLine />
            </>
          )}
          <MenuItem checked={sortDir === "asc"} onClick={() => { actions.sort(pill.id, sortDir === "asc" ? null : "asc"); close(); }}>{t("Sort ascending")}</MenuItem>
          <MenuItem checked={sortDir === "desc"} onClick={() => { actions.sort(pill.id, sortDir === "desc" ? null : "desc"); close(); }}>{t("Sort descending")}</MenuItem>
          <MenuLine />
          {pill.field !== "*" && !f?.is_aggregate && (
            measure
              ? <MenuItem onClick={() => set({ role: "dimension", agg: undefined, calc: undefined, grain: isDate(f) ? "month" : undefined })}>{t("Use as dimension")}</MenuItem>
              : <MenuItem onClick={() => set({ role: "measure", agg: isNumber(f) ? "sum" : "count_distinct", grain: undefined, part: undefined })}>{t("Use as measure ({v0})", { v0: isNumber(f) ? "sum" : t("count distinct") })}</MenuItem>
          )}
          {pill.field !== "*" && !f?.is_aggregate && <MenuItem onClick={() => { actions.filter(pill.field); close(); }}>{t("Filter…")}</MenuItem>}
          <MenuItem onClick={() => { actions.change(pill.id, null); close(); }}>{t("Remove")}</MenuItem>
        </Menu>
      )}
    </>
  );
}

// ------------------------------------------------------------------ a shelf (drop zone)
export function Shelf({ name, label, pills, fields, catalog, sort, actions, onDrop, vertical, hint }: {
  name: ShelfName | "filters";
  label: ReactNode;
  pills: Pill[];
  fields: Map<string, Field>;
  catalog?: Catalog;
  sort: { pill: string; dir: "asc" | "desc" } | null;
  actions: PillActions;
  onDrop: (payload: DragPayload, index: number) => void;
  vertical?: boolean;
  hint?: string;
}) {
  const [over, setOver] = useState(false);
  const drop = (e: DragEvent, index: number) => {
    e.preventDefault();
    e.stopPropagation();
    setOver(false);
    const payload = readDrag(e);
    if (payload) onDrop(payload, index);
  };
  return (
    <div data-shelf={name}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => drop(e, pills.length)}
      className={clsx("flex min-h-9 gap-2 rounded-lg border px-2 py-1.5 transition-colors",
        vertical ? "flex-col items-stretch" : "items-center",
        over ? "border-brand-400 bg-brand-50" : "border-slate-200 bg-white")}>
      <span className={clsx("shrink-0 text-xs font-medium text-slate-500", !vertical && "w-16")}>{label}</span>
      <div className={clsx("flex min-w-0 flex-1 flex-wrap gap-1.5", vertical && "flex-col items-start")}>
        {pills.map((p, i) => (
          <span key={p.id} onDrop={(e) => drop(e, i)} onDragOver={(e) => e.preventDefault()}>
            <PillChip pill={p} fields={fields} catalog={catalog} sortDir={sort?.pill === p.id ? sort.dir : null} actions={actions} />
          </span>
        ))}
        {!pills.length && <span className="text-xs text-slate-400">{hint ?? t("Drop fields here")}</span>}
      </div>
    </div>
  );
}
