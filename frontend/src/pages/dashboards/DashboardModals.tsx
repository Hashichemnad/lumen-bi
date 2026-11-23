import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { BarChart3, Globe, Image as ImageIcon, Plus, Type, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Alert, Button, Field as FormField, Input, Modal, Select } from "../../components/ui";
import type { SavedChart } from "../../lib/chart/types";
import { newId } from "../../lib/chart/types";
import { get, put } from "../../lib/api";
import type { Dashboard, DashFilter, FilterControl as Control, FilterValue, Tile } from "../../lib/dashboard/types";
import type { Principal } from "../../lib/types";
import { FilterControl } from "./FilterBar";

const message = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

// ------------------------------------------------------------------ add a tile
export function AddTileModal({ onAdd, onClose }: { onAdd: (tile: Omit<Tile, "desktop" | "id">, size: { w: number; h: number }) => void; onClose: () => void }) {
  const [kind, setKind] = useState<Tile["kind"]>("chart");
  const charts = useQuery({ queryKey: ["charts"], queryFn: () => get<SavedChart[]>("/charts") });
  const [url, setUrl] = useState("");
  const [alt, setAlt] = useState("");
  const kinds: [Tile["kind"], string, React.ReactNode][] = [
    ["chart", "Chart", <BarChart3 key="c" size={16} />], ["text", "Text", <Type key="t" size={16} />],
    ["image", "Image", <ImageIcon key="i" size={16} />], ["web", "Web page", <Globe key="w" size={16} />],
  ];
  const okUrl = /^https:\/\/\S+$/i.test(url.trim());
  return (
    <Modal open wide title="Add to the dashboard" onClose={onClose}
      footer={kind === "chart" ? <Button variant="secondary" onClick={onClose}>Close</Button> : <>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button disabled={kind !== "text" && !okUrl} onClick={() => {
          if (kind === "text") onAdd({ kind, text: "# Title\nWrite a note, a definition or a link." }, { w: 12, h: 2 });
          else onAdd({ kind, url: url.trim(), alt, fit: "contain" }, { w: kind === "web" ? 6 : 4, h: kind === "web" ? 8 : 4 });
          onClose();
        }}>Add</Button>
      </>}>
      <div className="grid grid-cols-4 gap-2">
        {kinds.map(([k, label, icon]) => (
          <button key={k} onClick={() => setKind(k)}
            className={clsx("flex flex-col items-center gap-1 rounded-lg border p-3 text-sm", kind === k ? "border-brand-600 bg-brand-50 text-brand-700" : "border-slate-200 hover:bg-slate-50")}>
            {icon}{label}
          </button>
        ))}
      </div>
      {kind === "chart" && (
        <div className="max-h-80 overflow-y-auto rounded-lg border border-slate-200">
          {charts.data?.map((c) => (
            <button key={c.id} onClick={() => { onAdd({ kind: "chart", chart_id: c.id, show_title: true }, { w: 6, h: 8 }); onClose(); }}
              className="flex w-full items-center justify-between border-b border-slate-100 px-3 py-2 text-left text-sm last:border-0 hover:bg-slate-50">
              <span className="font-medium">{c.name}</span>
              <span className="text-xs text-slate-500">{c.dataset_name}</span>
            </button>
          ))}
          {charts.data && !charts.data.length && (
            <p className="p-4 text-sm text-slate-500">You have no saved charts yet. <Link className="text-brand-600 hover:underline" to="/charts">Make a chart</Link> first.</p>
          )}
        </div>
      )}
      {kind === "text" && <p className="text-sm text-slate-600">Adds a text tile across the dashboard. You can write headings (#), lists (-), **bold** and links.</p>}
      {(kind === "image" || kind === "web") && (
        <div className="space-y-3">
          <FormField label="Address (https://…)" hint={kind === "web" ? "Some sites do not allow being shown inside other pages." : undefined}>
            <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" />
          </FormField>
          {kind === "image" && <FormField label="Description for screen readers"><Input value={alt} onChange={(e) => setAlt(e.target.value)} /></FormField>}
          {url && !okUrl && <p className="text-xs text-red-700">Use a full https:// address.</p>}
        </div>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------ tile settings
export function TileModal({ tile, onSave, onClose }: { tile: Tile; onSave: (t: Tile) => void; onClose: () => void }) {
  const [t, setT] = useState(tile);
  const set = (c: Partial<Tile>) => setT({ ...t, ...c });
  const urlOk = !t.url || /^https:\/\/\S+$/i.test(t.url);
  return (
    <Modal open wide title="Tile settings" onClose={onClose}
      footer={<>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button disabled={!urlOk} onClick={() => { onSave(t); onClose(); }}>Save</Button>
      </>}>
      {t.kind !== "text" && (
        <FormField label="Title" hint={t.kind === "chart" ? "Leave empty to use the chart's name." : undefined}>
          <Input value={t.title ?? ""} onChange={(e) => set({ title: e.target.value })} />
        </FormField>
      )}
      {t.kind === "chart" && (
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={t.show_title !== false} onChange={(e) => set({ show_title: e.target.checked })} /> Show the title</label>
      )}
      {t.kind === "text" && (
        <FormField label="Text" hint="# heading, ## smaller heading, - list item, **bold**, *italic*, [link](https://…)">
          <textarea value={t.text ?? ""} onChange={(e) => set({ text: e.target.value })} rows={8}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20" />
        </FormField>
      )}
      {(t.kind === "image" || t.kind === "web") && (
        <FormField label="Address (https://…)"><Input value={t.url ?? ""} onChange={(e) => set({ url: e.target.value })} /></FormField>
      )}
      {t.kind === "image" && (
        <>
          <FormField label="Description for screen readers"><Input value={t.alt ?? ""} onChange={(e) => set({ alt: e.target.value })} /></FormField>
          <FormField label="Fit">
            <Select value={t.fit ?? "contain"} onChange={(e) => set({ fit: e.target.value as Tile["fit"] })}>
              <option value="contain">Show the whole image</option>
              <option value="cover">Fill the tile (may crop)</option>
            </Select>
          </FormField>
        </>
      )}
      {!urlOk && <p className="text-xs text-red-700">Use a full https:// address.</p>}
    </Modal>
  );
}

// ------------------------------------------------------------------ dashboard filters
export function FilterModal({ dash, existing, onSave, onClose }: { dash: Dashboard; existing?: DashFilter; onSave: (f: DashFilter) => void; onClose: () => void }) {
  const datasets = Object.values(dash.datasets);
  const first = existing?.targets[0];
  const [datasetId, setDatasetId] = useState<number | "">(first?.dataset ?? (datasets[0]?.id ?? ""));
  const [field, setField] = useState(first?.field ?? "");
  const ds = datasets.find((d) => d.id === datasetId);
  const f = ds?.fields.find((x) => x.name === field);
  const isDate = !!f && ["date", "datetime"].includes(f.data_type);
  const isNum = !!f && ["integer", "number"].includes(f.data_type);
  const controls: [Control, string][] = isDate ? [["date", "Date presets and range"]] : isNum ? [["range", "Range (min – max)"], ["multi", "Pick values"]] : [["multi", "Pick values"], ["dropdown", "Pick one value"], ["search", "Search text"]];
  const [control, setControl] = useState<Control>(existing?.control ?? "multi");
  const ctl = controls.some(([c]) => c === control) ? control : controls[0]?.[0] ?? "multi";
  const kind: DashFilter["kind"] = ctl === "date" ? "relative" : ctl === "range" ? "range" : ctl === "search" ? "text" : "values";
  const [label, setLabel] = useState(existing?.label ?? "");
  // Other datasets on the dashboard: filter them by a field of the same name unless changed
  const [links, setLinks] = useState<Record<number, string>>(() => Object.fromEntries((existing?.targets ?? []).slice(1).map((t) => [t.dataset, t.field])));
  const [value, setValue] = useState<FilterValue>(existing?.value ?? null);
  const others = datasets.filter((d) => d.id !== datasetId);
  const linkFor = (id: number) => (id in links ? links[id] : others.find((d) => d.id === id)?.fields.some((x) => x.name === field) ? field : "");
  const draft: DashFilter | null = ds && f ? {
    id: existing?.id ?? newId("flt"),
    label: label || f.label,
    kind,
    control: ctl,
    targets: [{ dataset: ds.id, field: f.name }, ...others.map((o) => ({ dataset: o.id, field: linkFor(o.id) })).filter((t) => t.field)],
    value: existing && existing.kind === kind ? value : kind === "values" ? (Array.isArray(value) ? value : []) : value,
    grain: null,
  } : null;
  return (
    <Modal open wide title={existing ? "Edit filter" : "Add a filter"} onClose={onClose}
      footer={<>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button disabled={!draft} onClick={() => { if (draft) { onSave(draft); onClose(); } }}>{existing ? "Save" : "Add filter"}</Button>
      </>}>
      {!datasets.length ? (
        <Alert tone="blue">Add a chart first: filters apply to the datasets of the charts on the dashboard.</Alert>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3">
            <FormField label="Dataset">
              <Select value={datasetId} onChange={(e) => { setDatasetId(Number(e.target.value)); setField(""); }}>
                {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </Select>
            </FormField>
            <FormField label="Field">
              <Select value={field} onChange={(e) => { setField(e.target.value); setValue(null); }}>
                <option value="">Choose a field</option>
                {ds?.fields.filter((x) => !x.is_aggregate).map((x) => <option key={x.name} value={x.name}>{x.label}</option>)}
              </Select>
            </FormField>
          </div>
          {f && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <FormField label="Label"><Input value={label} placeholder={f.label} onChange={(e) => setLabel(e.target.value)} /></FormField>
                <FormField label="Control">
                  <Select value={ctl} onChange={(e) => { setControl(e.target.value as Control); setValue(null); }}>
                    {controls.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
                  </Select>
                </FormField>
              </div>
              {others.length > 0 && (
                <div>
                  <div className="mb-1.5 text-sm font-medium text-slate-700">Also filter charts of</div>
                  <div className="space-y-1.5">
                    {others.map((o) => (
                      <div key={o.id} className="flex items-center gap-2 text-sm">
                        <span className="w-40 truncate text-slate-600">{o.name}</span>
                        <div className="flex-1">
                          <Select className="py-1.5" value={linkFor(o.id)} onChange={(e) => setLinks({ ...links, [o.id]: e.target.value })}>
                            <option value="">Do not filter</option>
                            {o.fields.filter((x) => !x.is_aggregate).map((x) => <option key={x.name} value={x.name}>by {x.label}</option>)}
                          </Select>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {draft && dash.id > 0 && (
                <FormField label="Starting value (optional)">
                  <FilterControl dash={dash} f={draft} value={value} onChange={setValue} />
                </FormField>
              )}
            </>
          )}
        </>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------ sharing
interface Sharing {
  everyone: "none" | "view" | "edit";
  users: { id: number; level: "view" | "edit"; name: string; email: string }[];
  groups: { id: number; level: "view" | "edit"; name: string }[];
}

export function ShareModal({ dash, onClose }: { dash: Dashboard; onClose: () => void }) {
  const current = useQuery({ queryKey: ["dash-sharing", dash.id], queryFn: () => get<Sharing>(`/dashboards/${dash.id}/sharing`) });
  const directory = useQuery({ queryKey: ["directory"], queryFn: () => get<{ users: Principal[]; groups: Principal[] }>("/directory") });
  const [draft, setDraft] = useState<Sharing | null>(null);
  const [error, setError] = useState("");
  const s = draft ?? current.data;
  if (!s) return null;
  const set = (c: Partial<Sharing>) => setDraft({ ...s, ...c });
  const save = async () => {
    setError("");
    try {
      await put(`/dashboards/${dash.id}/sharing`, { everyone: s.everyone, users: s.users.map(({ id, level }) => ({ id, level })), groups: s.groups.map(({ id, level }) => ({ id, level })) });
      onClose();
    } catch (err) {
      setError(message(err, "Could not share"));
    }
  };
  const level = (v: string, onChange: (l: "view" | "edit") => void) => (
    <div className="w-32">
      <Select className="py-1" value={v} onChange={(e) => onChange(e.target.value as "view" | "edit")}>
        <option value="view">Can view</option>
        <option value="edit">Can edit</option>
      </Select>
    </div>
  );
  return (
    <Modal open wide title={`Share "${dash.name}"`} onClose={onClose}
      footer={<>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button onClick={save}>Save</Button>
      </>}>
      {error && <Alert>{error}</Alert>}
      <p className="text-sm text-slate-600">People see the charts through this dashboard with their own data access (row-level security). They do not need access to the datasets. Viewers can never edit.</p>
      <div className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-sm">
        <span>Everyone who can sign in</span>
        <div className="w-36">
          <Select className="py-1" value={s.everyone} onChange={(e) => set({ everyone: e.target.value as Sharing["everyone"] })}>
            <option value="none">No access</option>
            <option value="view">Can view</option>
            <option value="edit">Can edit</option>
          </Select>
        </div>
      </div>
      <div>
        <div className="mb-1.5 text-sm font-medium text-slate-700">People</div>
        {s.users.map((u) => (
          <div key={u.id} className="flex items-center gap-2 py-1 text-sm">
            <span className="flex-1 truncate">{u.name || u.email} <span className="text-slate-400">{u.email}</span></span>
            {level(u.level, (l) => set({ users: s.users.map((x) => (x.id === u.id ? { ...x, level: l } : x)) }))}
            <button onClick={() => set({ users: s.users.filter((x) => x.id !== u.id) })} className="rounded p-1 text-slate-400 hover:text-red-600" aria-label="Remove"><X size={14} /></button>
          </div>
        ))}
        <Select className="mt-1 py-1.5" value="" onChange={(e) => {
          const u = directory.data?.users.find((x) => x.id === Number(e.target.value));
          if (u) set({ users: [...s.users, { id: u.id, level: "view", name: u.name, email: u.email ?? "" }] });
        }}>
          <option value="">Add a person…</option>
          {directory.data?.users.filter((u) => u.id !== dash.owner_id && !s.users.some((x) => x.id === u.id)).map((u) => <option key={u.id} value={u.id}>{u.name ? `${u.name} (${u.email})` : u.email}</option>)}
        </Select>
      </div>
      <div>
        <div className="mb-1.5 text-sm font-medium text-slate-700">Groups</div>
        {s.groups.map((g) => (
          <div key={g.id} className="flex items-center gap-2 py-1 text-sm">
            <span className="flex-1 truncate">{g.name}</span>
            {level(g.level, (l) => set({ groups: s.groups.map((x) => (x.id === g.id ? { ...x, level: l } : x)) }))}
            <button onClick={() => set({ groups: s.groups.filter((x) => x.id !== g.id) })} className="rounded p-1 text-slate-400 hover:text-red-600" aria-label="Remove"><X size={14} /></button>
          </div>
        ))}
        <Select className="mt-1 py-1.5" value="" onChange={(e) => {
          const g = directory.data?.groups.find((x) => x.id === Number(e.target.value));
          if (g) set({ groups: [...s.groups, { id: g.id, level: "view", name: g.name }] });
        }}>
          <option value="">Add a group…</option>
          {directory.data?.groups.filter((g) => !s.groups.some((x) => x.id === g.id)).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        </Select>
      </div>
    </Modal>
  );
}

export { Plus };
