import { useQuery } from "@tanstack/react-query";
import { Building2, IdCard, KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { Alert, Button, Field, Input } from "../components/ui";
import { get } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { ProviderPublic } from "../lib/types";
import AuthShell from "./AuthShell";

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
          {ICONS[p.type]} Sign in with {p.name}
        </a>
      ))}
      <div className="flex items-center gap-3 py-2 text-xs text-slate-400">
        <span className="h-px flex-1 bg-slate-200" /> or use your email <span className="h-px flex-1 bg-slate-200" />
      </div>
    </div>
  );
}

export default function Login() {
  const { user, login } = useAuth();
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
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Sign in" subtitle="Use your work email and password.">
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      <SSOButtons next={from} />
      <form onSubmit={submit} className="space-y-4">
        <Field label="Email">
          <Input type="email" autoComplete="username" required autoFocus value={email}
            onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password">
          <Input type="password" autoComplete="current-password" required value={password}
            onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Button type="submit" className="w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
        <div className="text-center text-sm">
          <Link to="/forgot-password" className="text-brand-600 hover:underline">Forgot your password?</Link>
        </div>
      </form>
    </AuthShell>
  );
}
