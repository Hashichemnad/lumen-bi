import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";

import { Alert, Badge, Button, Card, Field, Input, Modal, PageHeader, Table } from "../../components/ui";
import { del, get, patch, post, put } from "../../lib/api";
import type { Group, Page, User } from "../../lib/types";

function GroupModal({ group, onClose }: { group: Group | "new"; onClose: () => void }) {
  const qc = useQueryClient();
  const isNew = group === "new";
  const [name, setName] = useState(isNew ? "" : group.name);
  const [description, setDescription] = useState(isNew ? "" : group.description);
  const [members, setMembers] = useState<number[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");

  const users = useQuery({ queryKey: ["users", "all"], queryFn: () => get<Page<User>>("/users?page_size=500") });
  const current = useQuery({
    queryKey: ["group-members", isNew ? 0 : group.id],
    queryFn: () => get<number[]>(`/groups/${(group as Group).id}/members`),
    enabled: !isNew,
  });
  useEffect(() => {
    if (current.data) setMembers(current.data);
  }, [current.data]);

  const save = async () => {
    setError("");
    try {
      const saved = isNew
        ? await post<Group>("/groups", { name, description })
        : await patch<Group>(`/groups/${group.id}`, { name, description });
      await put(`/groups/${saved.id}/members`, { user_ids: members });
      await qc.invalidateQueries({ queryKey: ["groups"] });
      await qc.invalidateQueries({ queryKey: ["users"] });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    }
  };
  const remove = async () => {
    if (isNew || !confirm(`Delete group "${group.name}"? Members keep their accounts.`)) return;
    await del(`/groups/${group.id}`);
    await qc.invalidateQueries({ queryKey: ["groups"] });
    onClose();
  };

  const visible = (users.data?.items ?? []).filter(
    (u) => !filter || `${u.name} ${u.email}`.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <Modal open wide title={isNew ? "New group" : `Edit ${group.name}`} onClose={onClose}
      footer={<>
        {!isNew && <Button variant="danger" className="mr-auto" onClick={remove}>Delete</Button>}
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button onClick={save}>Save</Button>
      </>}>
      {error && <Alert>{error}</Alert>}
      <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Finance team" /></Field>
      <Field label="Description"><Input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
      <Field label={`Members (${members.length})`}>
        <Input placeholder="Filter users" value={filter} onChange={(e) => setFilter(e.target.value)} className="mb-2" />
        <div className="max-h-64 space-y-1 overflow-y-auto rounded-lg border border-slate-200 p-2">
          {visible.map((u) => (
            <label key={u.id} className="flex items-center gap-2 rounded px-2 py-1 text-sm hover:bg-slate-50">
              <input type="checkbox" checked={members.includes(u.id)}
                onChange={(e) => setMembers(e.target.checked ? [...members, u.id] : members.filter((id) => id !== u.id))} />
              <span className="font-medium">{u.name || u.email}</span>
              {u.name && <span className="text-slate-500">{u.email}</span>}
            </label>
          ))}
        </div>
      </Field>
    </Modal>
  );
}

export default function Groups() {
  const groups = useQuery({ queryKey: ["groups"], queryFn: () => get<Group[]>("/groups") });
  const [editing, setEditing] = useState<Group | "new" | null>(null);
  return (
    <>
      <PageHeader title="Groups" description="Share dashboards and apply data rules to many people at once."
        actions={<Button onClick={() => setEditing("new")}><Plus size={16} /> New group</Button>} />
      <Card>
        <Table head={["Group", "Members", "Source"]}>
          {groups.data?.map((g) => (
            <tr key={g.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setEditing(g)}>
              <td className="px-4 py-3">
                <div className="font-medium">{g.name}</div>
                {g.description && <div className="text-slate-500">{g.description}</div>}
              </td>
              <td className="px-4 py-3">{g.member_count}</td>
              <td className="px-4 py-3"><Badge tone={g.source === "local" ? "slate" : "blue"}>{g.source}</Badge></td>
            </tr>
          ))}
        </Table>
        {groups.data?.length === 0 && <p className="p-6 text-center text-sm text-slate-500">No groups yet.</p>}
      </Card>
      {editing && <GroupModal group={editing} onClose={() => setEditing(null)} />}
    </>
  );
}
