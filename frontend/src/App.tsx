import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import Layout from "./components/Layout";
import { useAuth } from "./lib/auth";
import type { Role } from "./lib/types";
import Account from "./pages/Account";
import ConnectionPage from "./pages/data/ConnectionPage";
import DataHome from "./pages/data/DataHome";
import DatasetPage from "./pages/data/DatasetPage";
import Audit from "./pages/admin/Audit";
import Groups from "./pages/admin/Groups";
import SignInMethods from "./pages/admin/SignInMethods";
import Users from "./pages/admin/Users";
import ForgotPassword from "./pages/ForgotPassword";
import Home from "./pages/Home";
import Login from "./pages/Login";
import ResetPassword from "./pages/ResetPassword";

function RequireAuth({ children, role }: { children: ReactNode; role?: Role }) {
  const { user, loading, hasRole } = useAuth();
  const location = useLocation();
  if (loading) return <div className="p-8 text-sm text-slate-500">Loading…</div>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (role && !hasRole(role)) return <Navigate to="/" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route element={<RequireAuth><Layout /></RequireAuth>}>
        <Route index element={<Home />} />
        <Route path="account" element={<Account />} />
        <Route path="data" element={<RequireAuth role="creator"><DataHome /></RequireAuth>} />
        <Route path="data/connections/:id" element={<RequireAuth role="creator"><ConnectionPage /></RequireAuth>} />
        <Route path="data/datasets/:id" element={<RequireAuth role="creator"><DatasetPage /></RequireAuth>} />
        <Route path="admin/users" element={<RequireAuth role="admin"><Users /></RequireAuth>} />
        <Route path="admin/groups" element={<RequireAuth role="admin"><Groups /></RequireAuth>} />
        <Route path="admin/sign-in" element={<RequireAuth role="admin"><SignInMethods /></RequireAuth>} />
        <Route path="admin/audit" element={<RequireAuth role="admin"><Audit /></RequireAuth>} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
