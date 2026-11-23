import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ThemePicker } from "../../components/ThemePicker";
import { Alert, Button, Card, Field, PageHeader, Select } from "../../components/ui";
import { put } from "../../lib/api";
import { formatDay, hijriDay } from "../../lib/dates";
import { t } from "../../lib/i18n";
import { usePrefs, type RegionalSettings } from "../../lib/prefs";
import { EmbedKeysCard, NotificationsCard } from "./Integrations";

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export default function AdminSettings() {
  const { regional } = usePrefs();
  const qc = useQueryClient();
  const [s, setS] = useState<RegionalSettings>(regional);
  const [msg, setMsg] = useState<{ tone: "red" | "green"; text: string } | null>(null);
  useEffect(() => setS(regional), [regional]);
  const set = <K extends keyof RegionalSettings>(k: K, v: RegionalSettings[K]) => setS({ ...s, [k]: v });

  const save = async () => {
    setMsg(null);
    try {
      await put("/admin/settings/regional", s);
      await qc.invalidateQueries({ queryKey: ["settings"] });
      setMsg({ tone: "green", text: t("Settings saved.") });
    } catch (err) {
      setMsg({ tone: "red", text: err instanceof Error ? t(err.message) : t("Could not save") });
    }
  };
  const fyEnd = (s.fiscal_year_start + 10) % 12;
  const today = new Date();
  return (
    <>
      <PageHeader title={t("Settings")} description={t("Defaults for everyone. People can change their own theme, language and calendar in My account.")}
        actions={<Button onClick={save}>{t("Save")}</Button>} />
      {msg && <div className="mb-4"><Alert tone={msg.tone}>{msg.text}</Alert></div>}
      <div className="space-y-6">
        <Card className="p-5">
          <h2 className="text-base font-semibold">{t("Look")}</h2>
          <p className="mb-4 text-sm text-slate-500">{t("The theme and language people see until they choose their own.")}</p>
          <ThemePicker value={s.default_theme} onChange={(v) => v && set("default_theme", v)} />
          <div className="mt-4 max-w-xs">
            <Field label={t("Default language")}>
              <Select value={s.default_language} onChange={(e) => set("default_language", e.target.value as "en" | "ar")}>
                <option value="en">English</option>
                <option value="ar">العربية</option>
              </Select>
            </Field>
          </div>
        </Card>
        <Card className="p-5">
          <h2 className="text-base font-semibold">{t("Calendar")}</h2>
          <p className="mb-4 text-sm text-slate-500">{t("Used for date levels in charts, relative date filters and how dates are shown.")}</p>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label={t("Show dates as")} hint={t("Hijri dates use the Umm al-Qura calendar.")}>
              <Select value={s.calendar} onChange={(e) => set("calendar", e.target.value as RegionalSettings["calendar"])}>
                <option value="gregorian">{t("Gregorian")}</option>
                <option value="hijri">{t("Hijri")}</option>
                <option value="both">{t("Both")}</option>
              </Select>
            </Field>
            <Field label={t("Week starts on")}>
              <Select value={s.week_start} onChange={(e) => set("week_start", Number(e.target.value))}>
                {WEEKDAYS.map((d, i) => <option key={d} value={i + 1}>{t(d)}</option>)}
              </Select>
            </Field>
            <Field label={t("Fiscal year starts in")} hint={t("Fiscal year {from} – {to}", { from: t(MONTHS[s.fiscal_year_start - 1]), to: t(MONTHS[fyEnd]) })}>
              <Select value={s.fiscal_year_start} onChange={(e) => set("fiscal_year_start", Number(e.target.value))}>
                {MONTHS.map((m, i) => <option key={m} value={i + 1}>{t(m)}</option>)}
              </Select>
            </Field>
          </div>
          <fieldset className="mt-4">
            <legend className="mb-1 text-sm font-medium text-slate-700">{t("Weekend")}</legend>
            <div className="flex flex-wrap gap-2">
              {WEEKDAYS.map((d, i) => {
                const on = s.weekend.includes(i + 1);
                return (
                  <label key={d} className={`flex min-h-10 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm ${on ? "border-brand-600 bg-brand-50 text-brand-700" : "border-slate-200 bg-white text-slate-700"}`}>
                    <input type="checkbox" checked={on} className="accent-current"
                      onChange={(e) => set("weekend", e.target.checked ? [...s.weekend, i + 1].sort() : s.weekend.filter((x) => x !== i + 1))} />
                    {t(d)}
                  </label>
                );
              })}
            </div>
            <p className="mt-1 text-xs text-slate-500">{t("In the UAE the weekend is Saturday and Sunday. Calendar charts shade the weekend.")}</p>
          </fieldset>
          <p className="mt-4 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
            {t("Today")}: {formatDay(today, { dateStyle: "full" })}{s.calendar === "gregorian" ? ` · ${hijriDay(today, { dateStyle: "long" })}` : ""}
          </p>
        </Card>
        <NotificationsCard />
        <EmbedKeysCard />
      </div>
    </>
  );
}
