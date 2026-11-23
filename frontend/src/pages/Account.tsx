import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { ThemePicker } from "../components/ThemePicker";
import { Alert, Badge, Button, Card, Field, Input, PageHeader, Select } from "../components/ui";
import type { CalendarDisplay } from "../lib/dates";
import { t } from "../lib/i18n";
import { usePrefs, type Preferences } from "../lib/prefs";
import { post } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { User } from "../lib/types";
import { PASSWORD_HINT } from "./ResetPassword";

function Appearance() {
  const { preferences, regional, savePreferences } = usePrefs();
  const [error, setError] = useState("");
  const change = async (p: Preferences) => {
    setError("");
    const next = { ...preferences, ...p };
    for (const k of Object.keys(next) as (keyof Preferences)[]) if (!next[k]) delete next[k];
    try {
      await savePreferences(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not save"));
    }
  };
  return (
    <Card className="p-5 lg:col-span-2">
      <h2 className="font-semibold">{t("Appearance")}</h2>
      <p className="mb-4 text-sm text-slate-500">{t("Only for you. Changes apply at once.")}</p>
      {error && <div className="mb-3"><Alert>{error}</Alert></div>}
      <ThemePicker value={preferences.theme ?? null} allowDefault defaultTheme={regional.default_theme} onChange={(v) => change({ theme: v ?? undefined })} />
      {preferences.theme && (
        <button className="mt-2 text-sm font-medium text-brand-700 hover:underline" onClick={() => change({ theme: undefined })}>
          {t("Use the organisation's default")}
        </button>
      )}
      <div className="mt-4 grid max-w-xl gap-4 sm:grid-cols-2">
        <Field label={t("Language")}>
          <Select value={preferences.language ?? ""} onChange={(e) => change({ language: (e.target.value || undefined) as Preferences["language"] })}>
            <option value="">{t("Organisation default")} ({regional.default_language === "ar" ? "العربية" : t("English")})</option>
            <option value="en">English</option>
            <option value="ar">العربية</option>
          </Select>
        </Field>
        <Field label={t("Show dates as")}>
          <Select value={preferences.calendar ?? ""} onChange={(e) => change({ calendar: (e.target.value || undefined) as CalendarDisplay | undefined })}>
            <option value="">{t("Organisation default")}</option>
            <option value="gregorian">{t("Gregorian")}</option>
            <option value="hijri">{t("Hijri")}</option>
            <option value="both">{t("Both")}</option>
          </Select>
        </Field>
      </div>
    </Card>
  );
}

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
      setMsg({ tone: "green", text: t("Password changed. Your other sessions were signed out.") });
    } catch (err) {
      setMsg({ tone: "red", text: err instanceof Error ? err.message : t("Could not change password") });
    }
  }

  if (!user) return null;
  const attrs = Object.entries(user.attributes);
  return (
    <>
      <PageHeader title={t("My account")} />
      <div className="grid gap-6 lg:grid-cols-2">
        <Appearance />
        <Card className="p-5">
          <h2 className="font-semibold">{t("Profile")}</h2>
          <dl className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between"><dt className="text-slate-500">{t("Name")}</dt><dd>{user.name || "—"}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">{t("Email")}</dt><dd>{user.email}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">{t("Role")}</dt><dd className="capitalize">{user.role}</dd></div>
            <div className="flex justify-between gap-4"><dt className="text-slate-500">{t("Groups")}</dt>
              <dd className="flex flex-wrap justify-end gap-1">
                {user.groups.length ? user.groups.map((g) => <Badge key={g.id}>{g.name}</Badge>) : "—"}
              </dd>
            </div>
            <div className="flex justify-between gap-4"><dt className="text-slate-500">{t("Sign-in methods")}</dt>
              <dd className="flex flex-wrap justify-end gap-1">
                {user.auth_provider === "local" && <Badge>{t("Email and password")}</Badge>}
                {user.sign_in_methods.map((m) => <Badge key={m.provider_id} tone="blue">{m.provider_name}</Badge>)}
              </dd>
            </div>
            <div className="flex justify-between gap-4"><dt className="text-slate-500">{t("Data access")}</dt>
              <dd className="text-end">
                {attrs.length ? attrs.map(([k, v]) => <div key={k}>{k}: {Array.isArray(v) ? v.join(", ") : String(v)}</div>) : "—"}
              </dd>
            </div>
          </dl>
          <Button variant="secondary" className="mt-5" onClick={async () => { await logout(true); navigate("/login"); }}>
            {t("Sign out on all devices")}
          </Button>
        </Card>
        {user.auth_provider === "local" && (
          <Card className="p-5">
            <h2 className="font-semibold">{t("Change password")}</h2>
            <form onSubmit={changePassword} className="mt-3 space-y-4">
              {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
              <Field label={t("Current password")}>
                <Input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
              </Field>
              <Field label={t("New password")} hint={t(PASSWORD_HINT)}>
                <Input type="password" autoComplete="new-password" required value={next} onChange={(e) => setNext(e.target.value)} />
              </Field>
              <Button type="submit">{t("Change password")}</Button>
            </form>
          </Card>
        )}
      </div>
    </>
  );
}
