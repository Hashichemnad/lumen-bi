import clsx from "clsx";
import {
  Building2, ChevronDown, Database, History, Home, KeyRound, LayoutDashboard, LogOut, Menu, PieChart, Settings,
  Sparkles, Users, UsersRound, X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../lib/auth";
import { t } from "../lib/i18n";
import type { Role } from "../lib/types";
import { useWorkspace, type WorkspacePick } from "../lib/workspace";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  role?: Role;
}

const MAIN: NavItem[] = [
  { to: "/", label: "Home", icon: <Home size={18} /> },
  { to: "/dashboards", label: "Dashboards", icon: <LayoutDashboard size={18} /> },
  { to: "/charts", label: "Charts", icon: <PieChart size={18} />, role: "explorer" },
  { to: "/data", label: "Data", icon: <Database size={18} />, role: "creator" },
  { to: "/workspaces", label: "Workspaces", icon: <Building2 size={18} /> },
];
const ADMIN: NavItem[] = [
  { to: "/admin/users", label: "Users", icon: <Users size={18} />, role: "admin" },
  { to: "/admin/groups", label: "Groups", icon: <UsersRound size={18} />, role: "admin" },
  { to: "/admin/sign-in", label: "Sign-in methods", icon: <KeyRound size={18} />, role: "admin" },
  { to: "/admin/ai", label: "AI", icon: <Sparkles size={18} />, role: "admin" },
  { to: "/admin/settings", label: "Settings", icon: <Settings size={18} />, role: "admin" },
  { to: "/admin/audit", label: "Audit log", icon: <History size={18} />, role: "admin" },
];

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden="true" className="shrink-0">
      <rect width="28" height="28" rx="7" className="logo-mark fill-brand-600" />
      <path d="M9 7v14h10" className="stroke-white" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function NavGroup({ title, items, compact, onPick }: { title?: string; items: NavItem[]; compact?: boolean; onPick?: () => void }) {
  const { hasRole } = useAuth();
  const visible = items.filter((i) => !i.role || hasRole(i.role));
  if (!visible.length) return null;
  return (
    <div className="space-y-0.5">
      {title && (compact ? <div className="mx-2 my-3 border-t border-slate-200" /> : (
        <p className="px-3 pb-1 pt-5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{t(title)}</p>
      ))}
      {visible.map((item) => (
        <NavLink key={item.to} to={item.to} end={item.to === "/"} title={compact ? t(item.label) : undefined} onClick={onPick}
          aria-label={compact ? t(item.label) : undefined}
          className={({ isActive }) =>
            clsx("flex min-h-10 items-center gap-3 rounded-lg px-3 py-2 text-sm",
              compact && "justify-center px-0",
              isActive ? "bg-brand-50 font-semibold text-brand-700" : "font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900")}>
          {item.icon}
          {!compact && t(item.label)}
        </NavLink>
      ))}
    </div>
  );
}

function WorkspacePicker() {
  const { pick, setPick, list } = useWorkspace();
  const label = (p: WorkspacePick) => (p === "all" ? t("All workspaces") : p === "personal" ? t("Personal") : list.find((w) => w.id === p)?.name ?? "");
  return (
    <label className="relative mb-2 block rounded-lg border border-slate-200 bg-white px-3 py-1.5 hover:border-slate-300">
      <span className="block text-[11px] text-slate-500">{t("Workspace")}</span>
      <span className="flex items-center justify-between gap-2 text-sm font-semibold text-slate-900">
        <span className="truncate">{label(pick)}</span>
        <ChevronDown size={14} className="shrink-0 text-slate-500" />
      </span>
      <select value={String(pick)} onChange={(e) => setPick(e.target.value === "all" || e.target.value === "personal" ? e.target.value : Number(e.target.value))}
        aria-label={t("Workspace")} className="absolute inset-0 cursor-pointer opacity-0">
        <option value="all">{t("All workspaces")}</option>
        <option value="personal">{t("Personal")}</option>
        {list.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
      </select>
    </label>
  );
}

function initials(name: string): string {
  return name.split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("") || "?";
}

function Sidebar({ compact, onPick }: { compact: boolean; onPick?: () => void }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const name = user?.name || user?.email || "";
  return (
    <>
      <div className={clsx("flex h-16 items-center gap-2.5", compact ? "justify-center" : "px-5")}>
        <Logo />
        {!compact && <span className="font-display text-[17px] font-bold tracking-tight text-slate-900">Lumen</span>}
      </div>
      <nav aria-label={t("Main")} className={clsx("flex-1 overflow-y-auto", compact ? "px-2" : "px-3")}>
        {!compact && <WorkspacePicker />}
        <NavGroup items={MAIN} compact={compact} onPick={onPick} />
        <NavGroup title={t("Administration")} items={ADMIN} compact={compact} onPick={onPick} />
      </nav>
      <div className={clsx("border-t border-slate-200", compact ? "p-2" : "p-3")}>
        <NavLink to="/account" onClick={onPick} title={compact ? t("My account") : undefined} aria-label={compact ? t("My account") : undefined}
          className={({ isActive }) => clsx("flex items-center gap-3 rounded-lg px-2 py-2 text-sm hover:bg-slate-100", compact && "justify-center", isActive && "bg-slate-100")}>
          <span aria-hidden="true" className="flex size-8 shrink-0 items-center justify-center rounded-full bg-brand-50 text-xs font-semibold text-brand-700">{initials(name)}</span>
          {!compact && (
            <span className="min-w-0">
              <span className="block truncate font-semibold text-slate-900">{name}</span>
              <span className="block truncate text-xs text-slate-500">{t({ viewer: "Viewer", explorer: "Explorer", creator: "Creator", admin: "Admin" }[user?.role ?? "viewer"])}</span>
            </span>
          )}
        </NavLink>
        <button
          onClick={async () => {
            await logout();
            navigate("/login");
          }}
          title={compact ? t("Sign out") : undefined}
          aria-label={compact ? t("Sign out") : undefined}
          className={clsx("mt-0.5 flex min-h-10 w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-100", compact && "justify-center px-0")}
        >
          <LogOut size={18} className="flip-rtl shrink-0" /> {!compact && t("Sign out")}
        </button>
      </div>
    </>
  );
}

export default function Layout() {
  // Work areas (the chart builder, a dashboard) use the full width, with an icon-only menu
  const location = useLocation();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [location.pathname]);
  const params = new URLSearchParams(location.search);
  const compact = /^\/(charts|dashboards)\/.+/.test(location.pathname);
  // The export renderer and TV screens open pages on their own: no menus
  if (params.get("print") === "1" || params.get("tv") === "1" || params.get("present") === "1") return <Outlet />;
  return (
    <div className="flex h-full">
      <aside className={clsx("hidden shrink-0 flex-col border-e border-slate-200 bg-white md:flex", compact ? "w-16" : "w-64")}>
        <Sidebar compact={compact} />
      </aside>
      {/* Phones: a top bar with the menu in a drawer */}
      {open && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button className="absolute inset-0 bg-black/40" aria-label={t("Close menu")} onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 start-0 flex w-72 max-w-[85vw] flex-col bg-white shadow-xl">
            <button onClick={() => setOpen(false)} className="absolute end-2 top-3 rounded-lg p-2 text-slate-500 hover:bg-slate-100" aria-label={t("Close menu")}><X size={18} /></button>
            <Sidebar compact={false} onPick={() => setOpen(false)} />
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-14 shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-3 md:hidden">
          <button onClick={() => setOpen(true)} className="rounded-lg p-2 text-slate-600 hover:bg-slate-100" aria-label={t("Open menu")}><Menu size={20} /></button>
          <Logo size={24} />
          <span className="font-display font-bold">Lumen</span>
        </div>
        <main className={clsx("min-w-0 flex-1", compact ? "overflow-hidden" : "overflow-y-auto")}>
          {compact ? <Outlet /> : (
            <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
              <Outlet />
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
