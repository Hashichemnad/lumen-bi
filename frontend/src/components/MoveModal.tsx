import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { put } from "../lib/api";
import { t } from "../lib/i18n";
import { useWorkspace } from "../lib/workspace";
import { Alert, Button, Modal, Select } from "./ui";

/** Put a dashboard or a chart in a workspace (or back to personal). */
export default function MoveModal({ kind, id, current, onClose, onMoved }: {
  kind: "dashboards" | "charts";
  id: number;
  current: number | null;
  onClose: () => void;
  onMoved: () => Promise<void> | void;
}) {
  const qc = useQueryClient();
  const { list } = useWorkspace();
  const [target, setTarget] = useState(current == null ? "" : String(current));
  const [error, setError] = useState("");
  const options = list.filter((w) => w.my_role !== "viewer");
  const save = async () => {
    try {
      await put(`/${kind}/${id}/workspace`, { workspace_id: target ? Number(target) : null });
      await onMoved();
      await qc.invalidateQueries({ queryKey: ["workspaces"] });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? t(err.message) : t("Could not move"));
    }
  };
  return (
    <Modal open title={t("Move to a workspace")} onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>{t("Cancel")}</Button><Button onClick={save}>{t("Move")}</Button></>}>
      {error && <Alert>{error}</Alert>}
      <label className="block text-sm">
        <span className="mb-1 block font-medium text-slate-700">{t("Workspace")}</span>
        <Select value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="">{t("Personal (only you and people you share with)")}</option>
          {options.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </Select>
      </label>
      <p className="text-sm text-slate-500">
        {kind === "dashboards"
          ? t("Members of the workspace get access by their role there. Charts on the dashboard stay where they are.")
          : t("Members of the workspace get access by their role there. They still see only the data they are allowed to.")}
      </p>
    </Modal>
  );
}
