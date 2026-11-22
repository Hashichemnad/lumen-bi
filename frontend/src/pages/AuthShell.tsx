import { BarChart3 } from "lucide-react";
import type { ReactNode } from "react";

export default function AuthShell({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <div className="flex min-h-full items-center justify-center bg-gradient-to-br from-slate-50 to-brand-50 p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2">
          <BarChart3 className="text-brand-600" size={28} />
          <span className="text-xl font-semibold tracking-tight">Lumen BI</span>
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h1 className="text-lg font-semibold">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
          <div className="mt-5">{children}</div>
        </div>
      </div>
    </div>
  );
}
