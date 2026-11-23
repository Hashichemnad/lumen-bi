import { useQuery } from "@tanstack/react-query";
import { Check, Eye, Lock, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Alert, Button } from "../components/ui";
import { get, post } from "../lib/api";
import { t } from "../lib/i18n";
import AuthShell from "./AuthShell";

interface RequestInfo {
  client_name: string;
  redirect_host: string;
  allowed: boolean;
  user: { name: string; email: string };
}

const KEYS = ["client_id", "redirect_uri", "response_type", "code_challenge", "code_challenge_method", "state", "scope", "resource"];

/** An AI app (such as Claude) asks to read Lumen as the signed-in person. */
export default function OAuthConsent() {
  const [search] = useSearchParams();
  const params = Object.fromEntries(KEYS.filter((k) => search.has(k)).map((k) => [k, search.get(k)!]));
  const info = useQuery({
    queryKey: ["oauth-request", search.toString()],
    queryFn: () => get<RequestInfo>(`/oauth/request?${new URLSearchParams(params).toString()}`),
    retry: false,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const answer = async (allow: boolean) => {
    setBusy(true);
    setError("");
    try {
      const r = await post<{ redirect: string }>(allow ? "/oauth/approve" : "/oauth/deny", params);
      window.location.assign(r.redirect);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Something went wrong"));
      setBusy(false);
    }
  };
  if (info.error) {
    return (
      <AuthShell title={t("This app cannot connect")}>
        <Alert>{(info.error as Error).message}</Alert>
      </AuthShell>
    );
  }
  if (!info.data) return <AuthShell title={t("Connect an app")}><p className="text-sm text-slate-500">{t("Loading…")}</p></AuthShell>;
  const d = info.data;
  return (
    <AuthShell title={t("{name} wants to use Lumen", { name: d.client_name })} subtitle={t("Signed in as {email}", { email: d.user.email })}>
      {!d.allowed ? (
        <div className="space-y-4">
          <Alert>{t("Your role cannot connect AI apps. Ask an admin if you need this.")}</Alert>
          <Button variant="secondary" className="w-full" onClick={() => answer(false)} disabled={busy}>{t("Go back")}</Button>
        </div>
      ) : (
        <div className="space-y-5">
          <ul className="space-y-3 text-sm text-slate-700">
            <li className="flex gap-3"><Eye size={18} className="mt-0.5 shrink-0 text-brand-600" /> {t("Search and read the dashboards, charts and data you can see")}</li>
            <li className="flex gap-3"><ShieldCheck size={18} className="mt-0.5 shrink-0 text-brand-600" /> {t("Your row-level security always applies: it sees only your rows")}</li>
            <li className="flex gap-3"><Lock size={18} className="mt-0.5 shrink-0 text-brand-600" /> {t("Read only: it cannot change or delete anything")}</li>
          </ul>
          <p className="text-xs text-slate-500">
            {t("Every request is recorded. You can disconnect it at any time in My account.")}{" "}
            {t("You will return to {host}.", { host: d.redirect_host })}
          </p>
          {error && <Alert>{error}</Alert>}
          <div className="flex gap-2">
            <Button variant="secondary" className="flex-1" onClick={() => answer(false)} disabled={busy}>{t("Cancel")}</Button>
            <Button className="flex-1" onClick={() => answer(true)} disabled={busy}><Check size={16} /> {t("Allow")}</Button>
          </div>
        </div>
      )}
    </AuthShell>
  );
}
