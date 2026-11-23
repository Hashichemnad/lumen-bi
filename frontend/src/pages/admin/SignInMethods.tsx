import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, Copy, IdCard, KeyRound, Plus } from "lucide-react";
import { useState, type ReactNode } from "react";

import KeyValueEditor, { toMap, toRows, type Row } from "../../components/KeyValueEditor";
import { Alert, Badge, Button, Card, Field, Input, Modal, PageHeader, Select } from "../../components/ui";
import { del, get, post, put } from "../../lib/api";
import { ROLES, type Provider, type ProviderType, type Role } from "../../lib/types";
import { t as tr } from "../../lib/i18n";

/** Built when drawn (not at load), so the texts follow the interface language. */
const types = (): Record<ProviderType, { label: string; icon: ReactNode; defaultName: string; help: ReactNode }> => ({
  entra: {
    label: "Microsoft Entra ID",
    defaultName: "Microsoft",
    icon: <Building2 size={20} />,
    help: (
      <ol className="list-decimal space-y-1 ps-5">
        <li>{tr("In the Microsoft Entra admin center, open App registrations → New registration.")}</li>
        <li>{tr("Supported account types:")} <b>{tr("this organisational directory only")}</b>.</li>
        <li>{tr("Redirect URI: platform")} <b>{tr("Web")}</b>{tr(", value = the callback URL below.")}</li>
        <li>{tr("Copy the Application (client) ID and Directory (tenant) ID here.")}</li>
        <li>{tr("Certificates & secrets → New client secret; paste its")} <b>{tr("value")}</b> {tr("here.")}</li>
        <li>{tr("Optional: Token configuration → Add groups claim, to map Microsoft groups to Lumen groups.")}</li>
      </ol>
    ),
  },
  uaepass: {
    label: "UAE PASS",
    defaultName: "UAE PASS",
    icon: <IdCard size={20} />,
    help: (
      <ol className="list-decimal space-y-1 ps-5">
        <li>{tr("For a first test on staging, client ID and secret are both")} <code>sandbox_stage</code>.</li>
        <li>{tr("For your own credentials, apply as a service provider on the UAE PASS developer portal and register the callback URL below.")}</li>
        <li>{tr("Switch to Production only after UAE PASS approves your go-live.")}</li>
      </ol>
    ),
  },
  oidc: {
    label: "Other OpenID Connect",
    defaultName: "Single sign-on",
    icon: <KeyRound size={20} />,
    help: <p>{tr("Any standard OpenID Connect provider (Okta, Google Workspace, Keycloak…). You need its discovery URL, a client ID and a secret.")}</p>,
  },
});

interface FormState {
  type: ProviderType;
  name: string;
  enabled: boolean;
  client_id: string;
  client_secret: string;
  config: Record<string, string>;
  attributes: Row[];
  groups: Row[];
  auto_create: boolean;
  default_role: Role;
  domains: string;
  link_by_email: boolean;
}

function initial(p: Provider | ProviderType): FormState {
  if (typeof p === "string") {
    return {
      type: p, name: types()[p].defaultName, enabled: false, client_id: p === "uaepass" ? "sandbox_stage" : "",
      client_secret: "", config: p === "uaepass" ? { environment: "staging", min_user_type: "SOP1", ui_locales: "en" } : {},
      attributes: [], groups: [], auto_create: false, default_role: "viewer", domains: "", link_by_email: true,
    };
  }
  return {
    type: p.type, name: p.name, enabled: p.enabled, client_id: p.client_id, client_secret: "", config: { ...p.config },
    attributes: toRows(p.attribute_map), groups: toRows(p.group_map), auto_create: p.auto_create,
    default_role: p.default_role, domains: p.allowed_domains.join(", "), link_by_email: p.link_by_email,
  };
}

function ProviderModal({ provider, onClose }: { provider: Provider | ProviderType; onClose: () => void }) {
  const qc = useQueryClient();
  const existing = typeof provider === "string" ? null : provider;
  const [f, setF] = useState<FormState>(() => initial(provider));
  const [error, setError] = useState("");
  const set = (patch: Partial<FormState>) => setF((prev) => ({ ...prev, ...patch }));
  const setConfig = (key: string, value: string) => set({ config: { ...f.config, [key]: value } });
  const meta = types()[f.type];

  const save = async () => {
    setError("");
    const body = {
      type: f.type, name: f.name, enabled: f.enabled, client_id: f.client_id,
      client_secret: f.client_secret ? f.client_secret : existing ? null : "",
      config: f.config, attribute_map: toMap(f.attributes), group_map: toMap(f.groups),
      auto_create: f.auto_create, default_role: f.default_role,
      allowed_domains: f.domains.split(",").map((d) => d.trim()).filter(Boolean), link_by_email: f.link_by_email,
    };
    try {
      if (existing) await put(`/admin/identity-providers/${existing.id}`, body);
      else await post("/admin/identity-providers", body);
      await qc.invalidateQueries({ queryKey: ["identity-providers"] });
      await qc.invalidateQueries({ queryKey: ["providers"] });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : tr("Could not save"));
    }
  };
  const remove = async () => {
    if (!existing || !confirm(tr("Remove {name}? People who only use it will no longer be able to sign in.", { name: existing.name }))) return;
    await del(`/admin/identity-providers/${existing.id}`);
    await qc.invalidateQueries({ queryKey: ["identity-providers"] });
    onClose();
  };

  return (
    <Modal open wide title={existing ? `Edit ${existing.name}` : `Add ${meta.label}`} onClose={onClose}
      footer={<>
        {existing && <Button variant="danger" className="me-auto" onClick={remove}>{tr("Remove")}</Button>}
        <Button variant="secondary" onClick={onClose}>{tr("Cancel")}</Button>
        <Button onClick={save}>{tr("Save")}</Button>
      </>}>
      {error && <Alert>{error}</Alert>}
      <Alert tone="blue">{meta.help}</Alert>
      {existing && (
        <Field label={tr("Callback (redirect) URL")} hint={tr("Register this exact URL with the provider.")}>
          <div className="flex gap-2">
            <Input readOnly value={existing.callback_url} onFocus={(e) => e.target.select()} />
            <Button type="button" variant="secondary" onClick={() => navigator.clipboard?.writeText(existing.callback_url)}>
              <Copy size={16} />
            </Button>
          </div>
        </Field>
      )}
      {!existing && <p className="text-sm text-slate-500">{tr("Save once to see the callback URL to register with the provider.")}</p>}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={tr("Button label")} hint={tr("Shown as \"Sign in with {v0}\"", { v0: f.name || "…" })}>
          <Input value={f.name} onChange={(e) => set({ name: e.target.value })} />
        </Field>
        <Field label={tr("Client ID")}><Input value={f.client_id} onChange={(e) => set({ client_id: e.target.value })} /></Field>
        <Field label={tr("Client secret")} hint={existing?.has_secret ? tr("A secret is stored. Leave empty to keep it.") : tr("Stored encrypted.")}>
          <Input type="password" autoComplete="new-password" value={f.client_secret}
            placeholder={existing?.has_secret ? "••••••••" : ""} onChange={(e) => set({ client_secret: e.target.value })} />
        </Field>

        {f.type === "entra" && (
          <Field label={tr("Directory (tenant) ID")} hint={tr("Your organisation's tenant ID or primary domain.")}>
            <Input value={f.config.tenant_id ?? ""} onChange={(e) => setConfig("tenant_id", e.target.value)} />
          </Field>
        )}
        {f.type === "oidc" && (
          <>
            <Field label={tr("Discovery URL")}>
              <Input placeholder={tr("https://idp.example.com/.well-known/openid-configuration")} value={f.config.discovery_url ?? ""}
                onChange={(e) => setConfig("discovery_url", e.target.value)} />
            </Field>
            <Field label={tr("Scopes")}><Input placeholder={tr("openid email profile")} value={f.config.scopes ?? ""}
              onChange={(e) => setConfig("scopes", e.target.value)} /></Field>
          </>
        )}
        {f.type === "uaepass" && (
          <>
            <Field label={tr("Environment")}>
              <Select value={f.config.environment ?? "staging"} onChange={(e) => setConfig("environment", e.target.value)}>
                <option value="staging">{tr("Staging (testing)")}</option>
                <option value="production">{tr("Production")}</option>
              </Select>
            </Field>
            <Field label={tr("Minimum account level")} hint={tr("SOP1 = basic (not verified); SOP2/SOP3 = Emirates ID verified.")}>
              <Select value={f.config.min_user_type ?? "SOP1"} onChange={(e) => setConfig("min_user_type", e.target.value)}>
                <option value="SOP1">{tr("Any account (SOP1+)")}</option>
                <option value="SOP2">{tr("Verified only (SOP2+)")}</option>
                <option value="SOP3">{tr("Qualified only (SOP3)")}</option>
              </Select>
            </Field>
            <Field label={tr("UAE PASS page language")}>
              <Select value={f.config.ui_locales ?? "en"} onChange={(e) => setConfig("ui_locales", e.target.value)}>
                <option value="en">English</option>
                <option value="ar">{tr("Arabic")}</option>
              </Select>
            </Field>
          </>
        )}
      </div>

      <div className="space-y-3 border-t border-slate-200 pt-4">
        <p className="text-sm font-medium text-slate-700">{tr("Who can sign in")}</p>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5" checked={f.auto_create} onChange={(e) => set({ auto_create: e.target.checked })} />
          <span>{tr("Create an account automatically on first sign-in")}<br />
            <span className="text-slate-500">{tr("Off = invite only: an admin must add the person first.")}</span></span>
        </label>
        {f.auto_create && (
          <Field label={tr("Role for new accounts")}>
            <Select value={f.default_role} onChange={(e) => set({ default_role: e.target.value as Role })}>
              {ROLES.map((r) => <option key={r.value} value={r.value}>{tr(r.label)}</option>)}
            </Select>
          </Field>
        )}
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5" checked={f.link_by_email} onChange={(e) => set({ link_by_email: e.target.checked })} />
          <span>{tr("Match existing accounts by email on first sign-in")}</span>
        </label>
        <Field label={tr("Allowed email domains (optional)")} hint={tr("Comma-separated, e.g. company.ae, company.com. Empty = any.")}>
          <Input value={f.domains} onChange={(e) => set({ domains: e.target.value })} />
        </Field>
      </div>

      <Field label={tr("Copy profile details into data-access attributes (optional)")}
        hint={tr("Updated at every sign-in. Example: attribute department ← claim department.")}>
        <KeyValueEditor rows={f.attributes} onChange={(attributes) => set({ attributes })}
          keyPlaceholder="Lumen attribute (e.g. department)" valuePlaceholder="Claim name (e.g. department)" addLabel="Add mapping" />
      </Field>
      {f.type !== "uaepass" && (
        <Field label={tr("Map provider groups to Lumen groups (optional)")}
          hint={tr("For Microsoft, use the group's Object ID. Memberships are updated at every sign-in.")}>
          <KeyValueEditor rows={f.groups} onChange={(groups) => set({ groups })}
            keyPlaceholder="Group ID from the provider" valuePlaceholder="Lumen group name" addLabel="Add group" />
        </Field>
      )}

      <label className="flex items-center gap-2 border-t border-slate-200 pt-4 text-sm font-medium">
        <input type="checkbox" checked={f.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
        {tr("Show this sign-in option on the login page")}
      </label>
    </Modal>
  );
}

export default function SignInMethods() {
  const providers = useQuery({ queryKey: ["identity-providers"], queryFn: () => get<Provider[]>("/admin/identity-providers") });
  const [editing, setEditing] = useState<Provider | ProviderType | null>(null);

  return (
    <>
      <PageHeader title={tr("Sign-in methods")} description={tr("Let people sign in with Microsoft or UAE PASS as well as email and password.")} />
      <Card className="mb-6 p-5">
        <h2 className="font-semibold">{tr("Add a sign-in method")}</h2>
        <div className="mt-3 flex flex-wrap gap-2">
          {(Object.keys(types()) as ProviderType[]).map((t) => (
            <Button key={t} variant="secondary" onClick={() => setEditing(t)}>
              <Plus size={16} /> {tr(types()[t].label)}
            </Button>
          ))}
        </div>
      </Card>
      <div className="space-y-3">
        <Card className="flex items-center gap-4 p-4">
          <KeyRound size={20} className="text-slate-500" />
          <div className="flex-1">
            <div className="font-medium">{tr("Email and password")}</div>
            <div className="text-sm text-slate-500">{tr("Always available for accounts with a password.")}</div>
          </div>
          <Badge tone="green">{tr("On")}</Badge>
        </Card>
        {providers.data?.map((p) => (
          <Card key={p.id} className="flex cursor-pointer items-center gap-4 p-4 hover:bg-slate-50">
            <button className="flex flex-1 items-center gap-4 text-start" onClick={() => setEditing(p)}>
              <span className="text-slate-500">{types()[p.type].icon}</span>
              <span className="flex-1">
                <span className="block font-medium">{p.name}</span>
                <span className="block text-sm text-slate-500">
                  {tr(types()[p.type].label)}
                  {p.type === "uaepass" && ` · ${p.config.environment === "production" ? tr("Production") : tr("Staging")}`}
                  {" · "}{p.auto_create ? `auto-create as ${p.default_role}` : tr("invite only")}
                </span>
              </span>
              {p.enabled ? <Badge tone="green">{tr("On")}</Badge> : <Badge>{tr("Off")}</Badge>}
            </button>
          </Card>
        ))}
      </div>
      {editing && <ProviderModal provider={editing} onClose={() => setEditing(null)} />}
    </>
  );
}
