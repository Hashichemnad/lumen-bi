import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { Alert, Button, Field, Input } from "../components/ui";
import { post } from "../lib/api";
import AuthShell from "./AuthShell";

export const PASSWORD_HINT = "At least 10 characters, with a letter and a number.";

export default function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) return setError("The passwords do not match");
    setError("");
    try {
      await post("/auth/reset-password", { token, new_password: password });
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    }
  }

  return (
    <AuthShell title="Set a new password">
      {done ? (
        <div className="space-y-4">
          <Alert tone="green">Your password has been set.</Alert>
          <Link to="/login"><Button className="w-full">Sign in</Button></Link>
        </div>
      ) : !token ? (
        <Alert>This link is missing its token. Request a new reset email.</Alert>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          {error && <Alert>{error}</Alert>}
          <Field label="New password" hint={PASSWORD_HINT}>
            <Input type="password" autoComplete="new-password" required value={password}
              onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <Field label="Confirm password">
            <Input type="password" autoComplete="new-password" required value={confirm}
              onChange={(e) => setConfirm(e.target.value)} />
          </Field>
          <Button type="submit" className="w-full">Save password</Button>
        </form>
      )}
    </AuthShell>
  );
}
