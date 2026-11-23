/**
 * The workspace picked in the side menu. "all" shows everything a person can open; "personal" shows items
 * that are in no workspace; a number shows one workspace. Remembered in this browser.
 */
import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useState, type ReactNode } from "react";

import { get } from "./api";
import { useAuth } from "./auth";

export type WorkspaceRole = "viewer" | "editor" | "manager";

export interface Workspace {
  id: number;
  name: string;
  description: string;
  my_role: WorkspaceRole;
  dashboards: number;
  charts: number;
  members: number;
  created_at: string;
}

export type WorkspacePick = "all" | "personal" | number;

interface WorkspaceState {
  pick: WorkspacePick;
  setPick: (p: WorkspacePick) => void;
  list: Workspace[];
  current: Workspace | null;
  /** Whether an item with this workspace id is shown under the current pick */
  shows: (workspaceId: number | null | undefined) => boolean;
  /** The workspace new items go into (null: personal) */
  target: number | null;
}

const KEY = "lumen.workspace";
const Ctx = createContext<WorkspaceState | null>(null);

function load(): WorkspacePick {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "personal" || v === "all") return v;
    if (v && /^\d+$/.test(v)) return Number(v);
  } catch {
    /* ignore */
  }
  return "all";
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [pick, setPickState] = useState<WorkspacePick>(load);
  const q = useQuery({ queryKey: ["workspaces"], queryFn: () => get<Workspace[]>("/workspaces"), enabled: !!user });
  const list = q.data ?? [];
  // A workspace that is gone (deleted, or no longer a member) falls back to everything
  const valid: WorkspacePick = typeof pick === "number" && q.data && !list.some((w) => w.id === pick) ? "all" : pick;
  const current = typeof valid === "number" ? (list.find((w) => w.id === valid) ?? null) : null;
  const setPick = (p: WorkspacePick) => {
    setPickState(p);
    try {
      localStorage.setItem(KEY, String(p));
    } catch {
      /* ignore */
    }
  };
  const value: WorkspaceState = {
    pick: valid,
    setPick,
    list,
    current,
    shows: (id) => (valid === "all" ? true : valid === "personal" ? id == null : id === valid),
    target: current && current.my_role !== "viewer" ? current.id : null,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWorkspace(): WorkspaceState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return ctx;
}
