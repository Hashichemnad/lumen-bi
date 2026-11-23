import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Download, Map as MapIcon, Trash2, Upload } from "lucide-react";
import { lazy, Suspense, useState } from "react";
import { Link } from "react-router-dom";

import { Alert, Button, Card, Field as FormField, Input, Modal, PageHeader, Select, Table, formatDate } from "../../components/ui";
import { api, del, get, patch } from "../../lib/api";
import { useAuth } from "../../lib/auth";

const MapPreview = lazy(() => import("./MapPreview"));

export interface Boundary {
  id: number;
  name: string;
  name_property: string;
  properties: string[];
  features: number;
  size_bytes: number;
  owner_id: number | null;
  created_at: string;
}

function UploadModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!file) return;
    setBusy(true);
    setError("");
    const form = new FormData();
    form.append("file", file);
    form.append("name", name || file.name.replace(/\.(geo)?json$/i, ""));
    try {
      await api("/boundaries", { method: "POST", form });
      await qc.invalidateQueries({ queryKey: ["boundaries"] });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open title="Upload a map" onClose={onClose}
      footer={<>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button disabled={!file || busy} onClick={submit}>{busy ? "Uploading…" : "Upload"}</Button>
      </>}>
      {error && <Alert>{error}</Alert>}
      <p className="text-sm text-slate-600">A GeoJSON file with the areas (emirates, districts, sales territories) and a property with each area's name. Up to 25 MB. Shapefiles and TopoJSON can be converted with mapshaper.org.</p>
      <FormField label="File (.geojson or .json)"><input type="file" accept=".geojson,.json,application/geo+json,application/json" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="text-sm" /></FormField>
      <FormField label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder={file?.name.replace(/\.(geo)?json$/i, "") ?? "UAE emirates"} /></FormField>
    </Modal>
  );
}

export default function MapsPage() {
  const qc = useQueryClient();
  const { user, hasRole } = useAuth();
  const list = useQuery({ queryKey: ["boundaries"], queryFn: () => get<Boundary[]>("/boundaries") });
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState<Boundary | null>(null);
  const [error, setError] = useState("");
  const canChange = (b: Boundary) => hasRole("admin") || b.owner_id === user?.id;
  const setProperty = async (b: Boundary, name_property: string) => {
    setError("");
    try {
      await patch(`/boundaries/${b.id}`, { name: b.name, name_property });
      await qc.invalidateQueries({ queryKey: ["boundaries"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    }
  };
  const remove = async (b: Boundary) => {
    if (!confirm(`Delete the map "${b.name}"?`)) return;
    setError("");
    try {
      await del(`/boundaries/${b.id}`);
      await qc.invalidateQueries({ queryKey: ["boundaries"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete");
    }
  };
  return (
    <>
      <Link to="/data" className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"><ArrowLeft size={14} /> Data</Link>
      <PageHeader title="Maps" description="Boundary files for Geo Map charts: emirates, districts, communities or your own sales territories."
        actions={<Button onClick={() => setUploading(true)}><Upload size={16} /> Upload a map</Button>} />
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <Card>
        {list.data && !list.data.length ? (
          <p className="p-8 text-center text-sm text-slate-500">No maps yet. Upload a GeoJSON file to shade areas on charts.</p>
        ) : (
          <Table head={["Name", "Areas", "Area names from", "Uploaded", ""]}>
            {list.data?.map((b) => (
              <tr key={b.id}>
                <td className="px-4 py-2.5"><button className="inline-flex items-center gap-1.5 font-medium text-brand-700 hover:underline" onClick={() => setPreview(b)}><MapIcon size={14} /> {b.name}</button></td>
                <td className="px-4 py-2.5 tabular-nums">{b.features}</td>
                <td className="px-4 py-2.5">
                  <div className="w-44">
                    <Select className="py-1" value={b.name_property} disabled={!canChange(b)} onChange={(e) => setProperty(b, e.target.value)}>
                      {b.properties.map((p) => <option key={p} value={p}>{p}</option>)}
                    </Select>
                  </div>
                </td>
                <td className="px-4 py-2.5 text-slate-500">{formatDate(b.created_at)}</td>
                <td className="px-4 py-2.5 text-right">
                  <a href={`/api/v1/boundaries/${b.id}/file`} className="inline-block rounded p-1.5 text-slate-500 hover:bg-slate-100" aria-label="Download"><Download size={15} /></a>
                  {canChange(b) && <button onClick={() => remove(b)} className="rounded p-1.5 text-slate-500 hover:bg-red-50 hover:text-red-600" aria-label="Delete"><Trash2 size={15} /></button>}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      {uploading && <UploadModal onClose={() => setUploading(false)} />}
      {preview && (
        <Modal open wide title={preview.name} onClose={() => setPreview(null)}>
          <Suspense fallback={<p className="text-sm text-slate-500">Loading…</p>}><MapPreview boundary={preview} /></Suspense>
        </Modal>
      )}
    </>
  );
}
