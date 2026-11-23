/**
 * Presentation and TV modes. Both show a dashboard one "slide" at a time, filling the screen:
 * a slide is a section (a section tile and the tiles under it) or, on a tab without sections, the whole tab.
 * - Presentation: arrow keys, space or the buttons move between slides; Esc leaves.
 * - TV: slides move on by themselves, data refreshes on a timer, a clock shows the time. For screens on a
 *   wall: open with ?tv=1 (&cycle=seconds, &refresh=minutes).
 */
import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ChevronLeft, ChevronRight, Maximize, Pause, Play, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { formatMoment } from "../../lib/dates";
import { locale, t } from "../../lib/i18n";
import type { DashConfig, Dashboard, Tab, Tile } from "../../lib/dashboard/types";
import { MARGIN } from "./DashboardTile";

/** A tab's tiles split at each section, in reading order. */
export function groupsOf(tab: Tab): Tile[][] {
  const ordered = [...tab.items].sort((a, b) => a.desktop.y - b.desktop.y || a.desktop.x - b.desktop.x);
  const groups: Tile[][] = [];
  for (const tile of ordered) {
    if (tile.kind === "section" || !groups.length) groups.push([tile]);
    else groups[groups.length - 1].push(tile);
  }
  return groups;
}

interface Slide {
  key: string;
  tab: Tab;
  tiles: Tile[];
  title: string;
}

export function slidesOf(cfg: DashConfig): Slide[] {
  const out: Slide[] = [];
  for (const tab of cfg.tabs) {
    if (!tab.items.length) continue;
    const groups = groupsOf(tab);
    const hasSections = groups.some((g) => g[0].kind === "section");
    if (!hasSections) {
      out.push({ key: tab.id, tab, tiles: tab.items, title: cfg.tabs.length > 1 ? tab.name : "" });
      continue;
    }
    for (const g of groups) {
      const head = g[0].kind === "section" ? g[0] : null;
      out.push({ key: `${tab.id}-${g[0].id}`, tab, tiles: g, title: head ? [head.code, head.title].filter(Boolean).join(" · ") : tab.name });
    }
  }
  return out;
}

/** Tiles placed on the 12-column grid, rows sized so the whole slide fits the height available. */
function SlideGrid({ tiles, height, render }: { tiles: Tile[]; height: number; render: (tile: Tile, heightPx: number) => React.ReactNode }) {
  const y0 = Math.min(...tiles.map((x) => x.desktop.y));
  const rows = Math.max(1, ...tiles.map((x) => x.desktop.y - y0 + x.desktop.h));
  const rowHeight = Math.max(24, Math.min(90, (height - (rows - 1) * MARGIN) / rows));
  return (
    <div className="grid" style={{ gridTemplateColumns: "repeat(12, minmax(0, 1fr))", gridAutoRows: `${rowHeight}px`, gap: MARGIN }}>
      {/* CSS grid columns follow the page direction, so right-to-left pages mirror the layout by themselves */}
      {tiles.map((tile) => (
        <div key={tile.id} style={{ gridColumn: `${tile.desktop.x + 1} / span ${tile.desktop.w}`, gridRow: `${tile.desktop.y - y0 + 1} / span ${tile.desktop.h}` }}>
          {render(tile, tile.desktop.h * rowHeight + (tile.desktop.h - 1) * MARGIN)}
        </div>
      ))}
    </div>
  );
}

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 15_000);
    return () => window.clearInterval(id);
  }, []);
  return <span className="tabular-nums">{new Intl.DateTimeFormat(locale(), { weekday: "short", hour: "2-digit", minute: "2-digit" }).format(now)}</span>;
}

export default function Stage({ dash, cfg, mode, render, onExit, refreshedAt }: {
  dash: Dashboard;
  cfg: DashConfig;
  mode: "present" | "tv";
  render: (tile: Tile, heightPx: number) => React.ReactNode;
  onExit: () => void;
  refreshedAt: string | null;
}) {
  const qc = useQueryClient();
  const slides = useMemo(() => slidesOf(cfg), [cfg]);
  const [index, setIndex] = useState(0);
  const params = new URLSearchParams(window.location.search);
  const cycle = Math.max(10, Number(params.get("cycle")) || 30);
  const refresh = Math.max(1, Number(params.get("refresh")) || 5);
  const [playing, setPlaying] = useState(mode === "tv");
  const box = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(window.innerHeight - 120);
  const [idle, setIdle] = useState(false);

  const go = useCallback((step: number) => setIndex((i) => (slides.length ? (i + step + slides.length) % slides.length : 0)), [slides.length]);

  useEffect(() => {
    const root = document.documentElement;
    if (mode === "tv") root.dataset.tv = "1";
    return () => {
      delete root.dataset.tv;
    };
  }, [mode]);
  useEffect(() => {
    const measure = () => setHeight((box.current?.clientHeight ?? window.innerHeight - 120) - 8);
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.fullscreenElement) onExit();
      else if (["ArrowRight", "PageDown", " "].includes(e.key)) { e.preventDefault(); go(document.dir === "rtl" && e.key === "ArrowRight" ? -1 : 1); }
      else if (["ArrowLeft", "PageUp"].includes(e.key)) { e.preventDefault(); go(document.dir === "rtl" && e.key === "ArrowLeft" ? 1 : -1); }
      else if (e.key.toLowerCase() === "f") document.documentElement.requestFullscreen?.().catch(() => undefined);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, onExit]);
  // TV: move on by itself, and keep the data fresh
  useEffect(() => {
    if (!playing || slides.length < 2) return;
    const id = window.setInterval(() => go(1), cycle * 1000);
    return () => window.clearInterval(id);
  }, [playing, slides.length, cycle, go]);
  useEffect(() => {
    if (mode !== "tv") return;
    const id = window.setInterval(() => {
      qc.invalidateQueries({ queryKey: ["dash-tile", dash.id] });
      qc.invalidateQueries({ queryKey: ["dashboard", dash.id] });
    }, refresh * 60_000);
    return () => window.clearInterval(id);
  }, [mode, refresh, dash.id, qc]);
  // Hide the controls and the pointer when nobody is using them
  useEffect(() => {
    let timer = window.setTimeout(() => setIdle(true), 3000);
    const wake = () => {
      setIdle(false);
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setIdle(true), 3000);
    };
    window.addEventListener("mousemove", wake);
    window.addEventListener("keydown", wake);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("mousemove", wake);
      window.removeEventListener("keydown", wake);
    };
  }, []);

  const slide = slides[Math.min(index, slides.length - 1)];
  return (
    <div className={clsx("fixed inset-0 z-50 flex flex-col bg-slate-50", idle && "cursor-none")}>
      <header className="flex items-center gap-4 border-b border-slate-200 bg-white px-6 py-3">
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-bold text-slate-900">{dash.name}</h1>
          {slide?.title && <p className="truncate text-sm text-slate-500">{slide.title}</p>}
        </div>
        {mode === "tv" && (
          <div className="hidden text-end text-sm text-slate-500 sm:block">
            <div className="text-lg font-semibold text-slate-900"><Clock /></div>
            {refreshedAt && <div>{t("Data refreshed {when}", { when: formatMoment(refreshedAt) })}</div>}
          </div>
        )}
        <div className={clsx("flex items-center gap-1 transition-opacity", idle && "opacity-0")}>
          {slides.length > 1 && (
            <button onClick={() => setPlaying((p) => !p)} className="rounded-lg p-2.5 text-slate-600 hover:bg-slate-100" aria-label={playing ? t("Pause") : t("Play")}>
              {playing ? <Pause size={18} /> : <Play size={18} />}
            </button>
          )}
          <button onClick={() => document.documentElement.requestFullscreen?.().catch(() => undefined)} className="rounded-lg p-2.5 text-slate-600 hover:bg-slate-100" aria-label={t("Full screen")}><Maximize size={18} /></button>
          <button onClick={onExit} className="rounded-lg p-2.5 text-slate-600 hover:bg-slate-100" aria-label={t("Leave")}><X size={18} /></button>
        </div>
      </header>
      <div ref={box} className="min-h-0 flex-1 overflow-hidden p-4 lg:p-6">
        {slide ? <SlideGrid key={slide.key} tiles={slide.tiles} height={height - 24} render={render} /> : <p className="p-10 text-center text-slate-500">{t("Nothing here yet.")}</p>}
      </div>
      {slides.length > 1 && (
        <footer className={clsx("flex items-center justify-center gap-3 pb-4 transition-opacity", idle && mode === "tv" && "opacity-0")}>
          <button onClick={() => go(-1)} className="rounded-full p-2 text-slate-600 hover:bg-slate-200" aria-label={t("Previous")}><ChevronLeft size={20} className="flip-rtl" /></button>
          <div className="flex gap-1.5" role="tablist" aria-label={t("Slides")}>
            {slides.map((s, i) => (
              <button key={s.key} role="tab" aria-selected={i === index} aria-label={s.title || t("Slide {n}", { n: i + 1 })} onClick={() => setIndex(i)}
                className={clsx("h-2 rounded-full transition-all", i === index ? "w-6 bg-brand-600" : "w-2 bg-slate-300 hover:bg-slate-400")} />
            ))}
          </div>
          <span className="w-14 text-center text-xs tabular-nums text-slate-500">{index + 1} / {slides.length}</span>
          <button onClick={() => go(1)} className="rounded-full p-2 text-slate-600 hover:bg-slate-200" aria-label={t("Next")}><ChevronRight size={20} className="flip-rtl" /></button>
        </footer>
      )}
    </div>
  );
}
