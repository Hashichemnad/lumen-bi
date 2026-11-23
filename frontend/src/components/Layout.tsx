import clsx from "clsx";
import { BarChart3, Database, PieChart, History, Home, KeyRound, LayoutDashboard, LogOut, UserCog, Users, UsersRound } from "lucide-react";
import type { ReactNode } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../lib/auth";
import type { Role } from "../lib/types";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  role?: Role;
  soon?: boolean;
}

const MAIN: NavItem[] = [
  { to: "/", label: "Home", icon: <Home size={18} /> },
  { to: "/charts", label: "Charts", icon: <PieChart size={18} />, role: "explorer" },
  { to: "/dashboards", label: "Dashboards", icon: <LayoutDashboard size={18} /> },
  { to: "/data", label: "Data", icon: <Database size={18} />, role: "creator" },
];
const ADMIN: NavItem[] = [
  { to: "/admin/users", label: "Users", icon: <Users size={18} />, role: "admin" },
  { to: "/admin/groups", label: "Groups", icon: <UsersRound size={18} />, role: "admin" },
  { to: "/admin/sign-in", label: "Sign-in methods", icon: <KeyRound size={18} />, role: "admin" },
  { to: "/admin/audit", label: "Audit log", icon: <History size={18} />, role: "admin" },
];

function NavGroup({ title, items, compact }: { title?: string; items: NavItem[]; compact?: boolean }) {
  const { hasRole } = useAuth();
  const visible = items.filter((i) => !i.role || hasRole(i.role));
  if (!visible.length) return null;
  return (
    <div className="space-y-1">
      {title && (compact ? <div className="mx-2 my-3 border-t border-slate-200" /> : <p className="px-3 pb-1 pt-4 text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</p>)}
      {visible.map((item) =>
        item.soon && compact ? null : item.soon ? (
          <span key={item.to} className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-slate-400"
            title="Coming in a later milestone">
            {item.icon}
            {item.label}
            <span className="ml-auto text-[10px] uppercase">soon</span>
          </span>
        ) : (
          <NavLink key={item.to} to={item.to} end={item.to === "/"} title={compact ? item.label : undefined}
            className={({ isActive }) =>
              clsx("flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium",
                isActive ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-100")}>
            {item.icon}
            {!compact && item.label}
          </NavLink>
        ),
      )}
    </div>
  );
}

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  // Work areas (the chart builder) use the full width, with an icon-only menu
  const location = useLocation();
  const compact = /^\/(charts|dashboards)\/.+/.test(location.pathname);
  // The export renderer opens pages in print view: the page alone, no menus
  if (new URLSearchParams(location.search).get("print") === "1") return <Outlet />;
  return (
    <div className="flex h-full">
      <aside className={clsx("flex shrink-0 flex-col border-r border-slate-200 bg-white", compact ? "w-14" : "w-60")}>
        <div className="flex h-14 items-center gap-2 border-b border-slate-200 px-4">
          <BarChart3 className="shrink-0 text-brand-600" size={22} />
          {!compact && <span className="font-semibold tracking-tight">Lumen BI</span>}
        </div>
        <nav className={clsx("flex-1 overflow-y-auto", compact ? "p-2" : "p-3")}>
          <NavGroup items={MAIN} compact={compact} />
          <NavGroup title="Administration" items={ADMIN} compact={compact} />
        </nav>
        <div className={clsx("border-t border-slate-200", compact ? "p-2" : "p-3")}>
          <NavLink to="/account" title={compact ? "Account" : undefined} className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm hover:bg-slate-100">
            <UserCog size={18} className="shrink-0 text-slate-500" />
            {!compact && (
              <span className="min-w-0">
                <span className="block truncate font-medium">{user?.name || user?.email}</span>
                <span className="block truncate text-xs capitalize text-slate-500">{user?.role}</span>
              </span>
            )}
          </NavLink>
          <button
            onClick={async () => {
              await logout();
              navigate("/login");
            }}
            className="mt-1 flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-100"
          >
            <LogOut size={18} className="shrink-0" /> {!compact && "Sign out"}
          </button>
        </div>
      </aside>
      <main className={clsx("min-w-0 flex-1", compact ? "overflow-hidden" : "overflow-y-auto")}>
        {compact ? <Outlet /> : (
          <div className="mx-auto max-w-6xl p-6 lg:p-8">
            <Outlet />
          </div>
        )}
      </main>
    </div>
  );
}
