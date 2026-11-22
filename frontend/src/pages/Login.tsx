import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";

import { Alert, Button, Field, Input } from "../components/ui";
import { useAuth } from "../lib/auth";
import AuthShell from "./AuthShell";

export default function Login() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
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
      <form onSubmit={submit} className="space-y-4">
        {error && <Alert>{error}</Alert>}
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
      {/* Microsoft and UAE PASS buttons are added in milestone 3 */}
    </AuthShell>
  );
}
