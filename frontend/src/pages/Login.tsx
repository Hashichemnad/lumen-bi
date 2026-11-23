import { useQuery } from "@tanstack/react-query";
import { Building2, IdCard, KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { CodeInput, TwoFactorSetup } from "../components/TwoFactor";
import { Alert, Button, Field, Input } from "../components/ui";
import { get, post } from "../lib/api";
import { useAuth, type TwoFactorStep } from "../lib/auth";
import { useBranding } from "../lib/branding";
import type { ProviderPublic, User } from "../lib/types";
import AuthShell from "./AuthShell";
import { t } from "../lib/i18n";

const ICONS = { entra: <Building2 size={18} />, uaepass: <IdCard size={18} />, oidc: <KeyRound size={18} /> };

// Official Microsoft / UAE PASS button artwork can replace these before go-live (UAE PASS approval
// checks that its button follows their branding guidelines).
function SSOButtons({ next }: { next: string }) {
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => get<ProviderPublic[]>("/auth/providers") });
  if (!providers.data?.length) return null;
  return (
    <div className="space-y-2">
      {providers.data.map((p) => (
        <a key={p.id} href={`/api/v1/auth/sso/${p.id}/start?next=${encodeURIComponent(next)}`}
          className="flex h-10 w-full items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white text-sm font-medium text-slate-700 hover:bg-slate-50">
          {ICONS[p.type]} {t("Sign in with {name}", { name: p.name })}
        </a>
      ))}
      <div className="flex items-center gap-3 py-2 text-xs text-slate-400">
        <span className="h-px flex-1 bg-slate-200" /> {t("or use your email")} <span className="h-px flex-1 bg-slate-200" />
      </div>
    </div>
  );
}

function CodeStep({ ticket, onDone, onBack }: { ticket: string; onDone: (u: User) => void; onBack: () => void }) {
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onDone(await post<User>("/auth/login/two-factor", { ticket, code }));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Sign-in failed"));
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="space-y-4">
      {error && <Alert>{error}</Alert>}
      <Field label={recovery ? t("Recovery code") : t("Code from your authenticator app")}>
        <CodeInput value={code} onChange={setCode} allowRecovery={recovery} />
      </Field>
      <Button type="submit" className="w-full" disabled={busy || !code.trim()}>{busy ? t("Checking…") : t("Sign in")}</Button>
      <div className="flex justify-between text-sm">
        <button type="button" className="text-brand-600 hover:underline" onClick={onBack}>{t("Back")}</button>
        <button type="button" className="text-brand-600 hover:underline" onClick={() => { setRecovery((r) => !r); setCode(""); }}>
          {recovery ? t("Use the app instead") : t("Use a recovery code")}
        </button>
      </div>
    </form>
  );
}

export default function Login() {
  const { user, login, signedIn } = useAuth();
  const [step, setStep] = useState<TwoFactorStep | null>(null);
  const branding = useBranding();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [params] = useSearchParams();
  const [error, setError] = useState(params.get("sso_error") ?? "");
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={from} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const next = await login(email, password);
      if (next) setStep(next);
      else navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Sign-in failed"));
    } finally {
      setBusy(false);
    }
  }

  const done = (u: User) => {
    signedIn(u);
    navigate(from, { replace: true });
  };
  if (step?.two_factor === "code") {
    return (
      <AuthShell title={t("Two-factor sign-in")} subtitle={t("Enter the code from your authenticator app.")}>
        <CodeStep ticket={step.ticket} onDone={done} onBack={() => setStep(null)} />
      </AuthShell>
    );
  }
  if (step?.two_factor === "setup") {
    return (
      <AuthShell title={t("Set up two-factor sign-in")} subtitle={t("Your organisation asks for a code from your phone at every sign-in.")}>
        <TwoFactorSetup ticket={step.ticket} onDone={done} />
      </AuthShell>
    );
  }

  return (
    <AuthShell title={t("Sign in")} subtitle={branding.login_message || t("Use your work email and password.")}>
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <SSOButtons next={from} />
      <form onSubmit={submit} className="space-y-4">
        <Field label={t("Email")}>
          <Input type="email" autoComplete="username" required autoFocus value={email}
            onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label={t("Password")}>
          <Input type="password" autoComplete="current-password" required value={password}
            onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Button type="submit" className="w-full" disabled={busy}>{busy ? t("Signing in…") : t("Sign in")}</Button>
        <div className="text-center text-sm">
          <Link to="/forgot-password" className="text-brand-600 hover:underline">{t("Forgot your password?")}</Link>
        </div>
      </form>
    </AuthShell>
  );
}
