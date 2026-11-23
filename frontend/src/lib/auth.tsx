import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";

import { ApiError, api, get, post } from "./api";
import { ROLE_LEVEL, type Role, type User } from "./types";

interface AuthState {
  user: User | null;
  loading: boolean;
  /** Signs in, or returns the next step when two-factor sign-in is on (or must be set up). */
  login: (email: string, password: string) => Promise<TwoFactorStep | null>;
  /** Finish signing in with the user the second step returned */
  signedIn: (user: User) => void;
  /** Signs out; for SSO sessions the browser is sent to the provider's logout page. */
  logout: (everywhere?: boolean) => Promise<void>;
  hasRole: (minimum: Role) => boolean;
}

export interface TwoFactorStep {
  two_factor: "code" | "setup";
  ticket: string;
}

const AuthContext = createContext<AuthState | null>(null);

async function fetchMe(): Promise<User | null> {
  try {
    return await get<User>("/auth/me");
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      // Access token may have expired: try the refresh token once
      try {
        return await post<User>("/auth/refresh");
      } catch {
        return null;
      }
    }
    throw e;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["me"], queryFn: fetchMe, staleTime: 60_000 });
  const user = data ?? null;

  const value: AuthState = {
    user,
    loading: isLoading,
    login: async (email, password) => {
      const res = await api<User | TwoFactorStep>("/auth/login", { method: "POST", body: { email, password } });
      if ("two_factor" in res) return res;
      qc.setQueryData(["me"], res);
      return null;
    },
    signedIn: (me) => qc.setQueryData(["me"], me),
    logout: async (everywhere = false) => {
      const res = await post<{ logout_url: string | null }>(`/auth/logout${everywhere ? "?everywhere=true" : ""}`).catch(
        () => null,
      );
      // Keep the "me" query (its observer stays attached) but drop everything cached for this user
      qc.setQueryData(["me"], null);
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
      if (res?.logout_url) window.location.assign(res.logout_url);
    },
    hasRole: (minimum) => !!user && ROLE_LEVEL[user.role] >= ROLE_LEVEL[minimum],
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
