import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { Alert, Badge, Button, Card, Field, Input, PageHeader } from "../components/ui";
import { post } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { User } from "../lib/types";
import { PASSWORD_HINT } from "./ResetPassword";

export default function Account() {
  const { user, logout } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<{ tone: "red" | "green"; text: string } | null>(null);

  async function changePassword(e: FormEvent) {
    e.preventDefault();
    try {
      const me = await post<User>("/auth/change-password", { current_password: current, new_password: next });
      qc.setQueryData(["me"], me);
      setCurrent("");
      setNext("");
      setMsg({ tone: "green", text: "Password changed. Your other sessions were signed out." });
    } catch (err) {
      setMsg({ tone: "red", text: err instanceof Error ? err.message : "Could not change password" });
    }
  }

  if (!user) return null;
  const attrs = Object.entries(user.attributes);
  return (
    <>
      <PageHeader title="My account" />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <h2 className="font-semibold">Profile</h2>
          <dl className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between"><dt className="text-slate-500">Name</dt><dd>{user.name || "—"}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">Email</dt><dd>{user.email}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">Role</dt><dd className="capitalize">{user.role}</dd></div>
            <div className="flex justify-between gap-4"><dt className="text-slate-500">Groups</dt>
              <dd className="flex flex-wrap justify-end gap-1">
                {user.groups.length ? user.groups.map((g) => <Badge key={g.id}>{g.name}</Badge>) : "—"}
              </dd>
            </div>
            <div className="flex justify-between gap-4"><dt className="text-slate-500">Sign-in methods</dt>
              <dd className="flex flex-wrap justify-end gap-1">
                {user.auth_provider === "local" && <Badge>Email and password</Badge>}
                {user.sign_in_methods.map((m) => <Badge key={m.provider_id} tone="blue">{m.provider_name}</Badge>)}
              </dd>
            </div>
            <div className="flex justify-between gap-4"><dt className="text-slate-500">Data access</dt>
              <dd className="text-right">
                {attrs.length ? attrs.map(([k, v]) => <div key={k}>{k}: {Array.isArray(v) ? v.join(", ") : String(v)}</div>) : "—"}
              </dd>
            </div>
          </dl>
          <Button variant="secondary" className="mt-5" onClick={async () => { await logout(true); navigate("/login"); }}>
            Sign out on all devices
          </Button>
        </Card>
        {user.auth_provider === "local" && (
          <Card className="p-5">
            <h2 className="font-semibold">Change password</h2>
            <form onSubmit={changePassword} className="mt-3 space-y-4">
              {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
              <Field label="Current password">
                <Input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
              </Field>
              <Field label="New password" hint={PASSWORD_HINT}>
                <Input type="password" autoComplete="new-password" required value={next} onChange={(e) => setNext(e.target.value)} />
              </Field>
              <Button type="submit">Change password</Button>
            </form>
          </Card>
        )}
      </div>
    </>
  );
}
