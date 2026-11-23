import { Check, Copy, Download, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { post } from "../lib/api";
import { t } from "../lib/i18n";
import type { User } from "../lib/types";
import { Alert, Button, Field, Input } from "./ui";

interface SetupInfo {
  secret: string;
  uri: string;
  qr_svg: string;
}

/** A 6-digit code box (also takes a recovery code). */
export function CodeInput({ value, onChange, allowRecovery }: { value: string; onChange: (v: string) => void; allowRecovery?: boolean }) {
  return (
    <Input value={value} onChange={(e) => onChange(e.target.value)} autoFocus required dir="ltr"
      inputMode={allowRecovery ? "text" : "numeric"} autoComplete="one-time-code" maxLength={allowRecovery ? 20 : 7}
      placeholder={allowRecovery ? "123456" : "123 456"} className="text-center font-mono text-lg tracking-[0.3em]" />
  );
}

/** Shown once: the recovery codes, to print or save. */
export function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  const text = codes.join("\n");
  return (
    <div className="space-y-4">
      <Alert tone="blue">{t("Keep these recovery codes somewhere safe. Each one signs you in once if you lose your phone. They are shown only now.")}</Alert>
      <ul className="grid grid-cols-2 gap-2 rounded-lg bg-slate-50 p-3 font-mono text-sm" dir="ltr">
        {codes.map((c) => <li key={c}>{c}</li>)}
      </ul>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="secondary" size="sm" onClick={async () => { await navigator.clipboard.writeText(text); setCopied(true); }}>
          {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? t("Copied") : t("Copy")}
        </Button>
        <Button type="button" variant="secondary" size="sm" onClick={() => {
          const a = document.createElement("a");
          a.href = URL.createObjectURL(new Blob([text + "\n"], { type: "text/plain" }));
          a.download = "lumen-recovery-codes.txt";
          a.click();
        }}><Download size={14} /> {t("Download")}</Button>
      </div>
      <Button type="button" className="w-full" onClick={onDone}>{t("I have saved them")}</Button>
    </div>
  );
}

/** Set up an authenticator app: scan, type a code, save the recovery codes. With a ticket this is part of
 * signing in; without, the signed-in person turns it on from My account. */
export function TwoFactorSetup({ ticket, onDone }: { ticket?: string; onDone: (user: User) => void }) {
  const [info, setInfo] = useState<SetupInfo | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ user: User; recovery_codes: string[] } | null>(null);
  const [showKey, setShowKey] = useState(false);
  useEffect(() => {
    post<SetupInfo>("/auth/two-factor/setup", { ticket: ticket ?? null }).then(setInfo, (e) => setError((e as Error).message));
  }, [ticket]);
  if (done) return <RecoveryCodes codes={done.recovery_codes} onDone={() => onDone(done.user)} />;
  const confirm = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      setDone(await post<{ user: User; recovery_codes: string[] }>("/auth/two-factor/enable", { ticket: ticket ?? null, code }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={confirm} className="space-y-4">
      <ol className="list-decimal space-y-1 ps-5 text-sm text-slate-600">
        <li>{t("Open an authenticator app on your phone (Microsoft Authenticator, Google Authenticator or similar).")}</li>
        <li>{t("Scan this code, then type the 6 digits the app shows.")}</li>
      </ol>
      {info ? (
        <div className="flex flex-col items-center gap-2">
          <img src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(info.qr_svg)}`} alt={t("QR code")} className="h-48 w-48 rounded-lg border border-slate-200 bg-white p-2" />
          <button type="button" className="text-xs text-brand-700 hover:underline" onClick={() => setShowKey((v) => !v)}>{t("Can't scan it?")}</button>
          {showKey && <code className="select-all break-all rounded bg-slate-100 px-2 py-1 text-xs" dir="ltr">{info.secret}</code>}
        </div>
      ) : !error && <div className="mx-auto h-48 w-48 animate-pulse rounded-lg bg-slate-100" />}
      {error && <Alert>{error}</Alert>}
      <Field label={t("Code from the app")}><CodeInput value={code} onChange={setCode} /></Field>
      <Button type="submit" className="w-full" disabled={busy || !info || code.replace(/\D/g, "").length !== 6}>
        <ShieldCheck size={16} /> {busy ? t("Checking…") : t("Turn on two-factor sign-in")}
      </Button>
    </form>
  );
}
