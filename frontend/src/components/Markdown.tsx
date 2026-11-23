import type { ReactNode } from "react";

/** A small, safe subset of Markdown for dashboard text tiles: # and ## headings, - lists, **bold**,
 * *italic*, [links](https://…). Builds React elements; never inserts HTML. */
function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*([^*]+)\*\*|\*([^*]+)\*|\[([^\]]+)\]\((https:\/\/[^\s)]+)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(<strong key={`${key}-${i++}`}>{m[1]}</strong>);
    else if (m[2]) out.push(<em key={`${key}-${i++}`}>{m[2]}</em>);
    else out.push(<a key={`${key}-${i++}`} href={m[4]} target="_blank" rel="noopener noreferrer" className="text-brand-600 underline">{m[3]}</a>);
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export default function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = (k: number) => {
    if (list.length) {
      blocks.push(<ul key={`ul${k}`} className="list-disc space-y-0.5 ps-5">{list.map((l, i) => <li key={i}>{inline(l, `li${k}-${i}`)}</li>)}</ul>);
      list = [];
    }
  };
  text.split("\n").forEach((line, k) => {
    const t = line.trimEnd();
    if (/^\s*[-*] /.test(t)) {
      list.push(t.replace(/^\s*[-*] /, ""));
      return;
    }
    flush(k);
    if (t.startsWith("## ")) blocks.push(<h3 key={k} className="text-base font-semibold text-slate-900">{inline(t.slice(3), `h${k}`)}</h3>);
    else if (t.startsWith("# ")) blocks.push(<h2 key={k} className="text-xl font-semibold text-slate-900">{inline(t.slice(2), `h${k}`)}</h2>);
    else if (t) blocks.push(<p key={k}>{inline(t, `p${k}`)}</p>);
  });
  flush(-1);
  return <div className="space-y-2 text-sm leading-relaxed text-slate-700">{blocks}</div>;
}
