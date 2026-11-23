/**
 * Dashboards without a sign-in:
 * - /embed/12#token=…   inside another application (the token is signed with an embed key)
 * - /display#…          a TV screen (a display link made by an admin); opens in TV mode
 * The token from the address is exchanged for a short embed session that can only read that dashboard.
 * Display sessions are renewed in the background so a screen can run for days.
 */
import { lazy, Suspense, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { post, setEmbedToken } from "../lib/api";
import { t } from "../lib/i18n";

const DashboardPage = lazy(() => import("./dashboards/DashboardPage"));

interface Session {
  token: string;
  dashboard_id: number;
  expires_in: number;
}

let started: Promise<Session> | null = null;

function readHash(): string {
  const raw = window.location.hash.slice(1);
  // Keep the token out of the address bar (and out of history) once read
  if (raw) window.history.replaceState(null, "", window.location.pathname + window.location.search);
  return raw.startsWith("token=") ? decodeURIComponent(raw.slice(6)) : decodeURIComponent(raw);
}

function startSession(body: { token?: string; display?: string }): Promise<Session> {
  started ??= post<Session>("/embed/session", body);
  return started;
}

export function DisplayPage() {
  const navigate = useNavigate();
  const [error, setError] = useState("");
  useEffect(() => {
    const display = readHash();
    if (!display) {
      setError(t("This display link is not valid"));
      return;
    }
    startSession({ display })
      .then((s) => {
        setEmbedToken(s.token);
        // Renew well before the session ends
        window.setInterval(async () => {
          try {
            const next = await post<Session>("/embed/session", { display });
            setEmbedToken(next.token);
          } catch {
            /* the link was revoked: the next data refresh shows the error */
          }
        }, Math.max(60, s.expires_in / 2) * 1000);
        navigate(`/embed/${s.dashboard_id}?tv=1`, { replace: true });
      })
      .catch((e) => setError(e instanceof Error ? e.message : t("This display link is not valid")));
  }, [navigate]);
  return <Message text={error || t("Loading…")} />;
}

export default function EmbedPage() {
  const id = Number(useParams().id);
  const [ready, setReady] = useState(!!started);
  const [error, setError] = useState("");
  useEffect(() => {
    if (started) {
      started.then(() => setReady(true)).catch(() => setError(t("This embed link is not valid")));
      return;
    }
    const token = readHash();
    if (!token) {
      setError(t("This embed link is not valid"));
      return;
    }
    startSession({ token })
      .then((s) => {
        if (s.dashboard_id !== id) throw new Error(t("This embed link is not valid"));
        setEmbedToken(s.token);
        setReady(true);
      })
      .catch((e) => setError(e instanceof Error ? e.message : t("This embed link is not valid")));
  }, [id]);
  if (error) return <Message text={error} />;
  if (!ready) return <Message text={t("Loading…")} />;
  return <Suspense fallback={<Message text={t("Loading…")} />}><div className="h-full"><DashboardPage key={id} /></div></Suspense>;
}

function Message({ text }: { text: string }) {
  return <div className="flex h-full items-center justify-center p-6 text-sm text-slate-500">{text}</div>;
}
