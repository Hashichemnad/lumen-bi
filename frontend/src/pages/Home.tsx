import { CheckCircle2, Circle } from "lucide-react";

import { Card, PageHeader } from "../components/ui";
import { useAuth } from "../lib/auth";

const ROADMAP = [
  { name: "Sign-in, users, roles and groups", done: true },
  { name: "Microsoft and UAE PASS sign-in", done: true },
  { name: "Data connections: files, Oracle, PostgreSQL, scheduled sync", done: true },
  { name: "Salesforce and REST API connections", done: true },
  { name: "Data modelling and calculated fields", done: true },
  { name: "Query engine with row-level security", done: true },
  { name: "Chart builder", done: true },
  { name: "Interactive dashboards", done: false },
  { name: "Exports, email reports and alerts", done: false },
  { name: "AI: ask questions, explain, forecast", done: false },
];

export default function Home() {
  const { user } = useAuth();
  return (
    <>
      <PageHeader title={`Welcome, ${user?.name || user?.email}`} description="Your dashboards will appear here." />
      <Card className="p-5">
        <h2 className="font-semibold">Build progress</h2>
        <ul className="mt-3 space-y-2">
          {ROADMAP.map((item) => (
            <li key={item.name} className="flex items-center gap-2 text-sm">
              {item.done ? <CheckCircle2 size={18} className="text-emerald-600" /> : <Circle size={18} className="text-slate-300" />}
              <span className={item.done ? "" : "text-slate-500"}>{item.name}</span>
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}
