import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { Alert, Button, Field, Input } from "../components/ui";
import { post } from "../lib/api";
import AuthShell from "./AuthShell";

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await post("/auth/forgot-password", { email });
      setSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    }
  }

  return (
    <AuthShell title="Reset your password" subtitle="We'll email you a link to set a new password.">
      {sent ? (
        <Alert tone="green">If an account exists for {email}, a reset link is on its way.</Alert>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          {error && <Alert>{error}</Alert>}
          <Field label="Email">
            <Input type="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Button type="submit" className="w-full">Send reset link</Button>
        </form>
      )}
      <div className="mt-4 text-center text-sm">
        <Link to="/login" className="text-brand-600 hover:underline">Back to sign in</Link>
      </div>
    </AuthShell>
  );
}
