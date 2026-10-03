"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { siteInfo } from "@/lib/local";

export const PIPELINE: { status: string; label: string; color: string; hint: string }[] = [
  { status: "TO_CONTACT", label: "À contacter", color: "bg-emerald-500", hint: "" },
  { status: "CONTACTED", label: "Contacté", color: "bg-sky-500", hint: "en attente de réponse" },
  { status: "REPLIED", label: "Réponse reçue", color: "bg-violet-500", hint: "" },
  { status: "INTERESTED", label: "Intéressé", color: "bg-amber-500", hint: "devis, rendez-vous…" },
  { status: "WON", label: "Client obtenu", color: "bg-yellow-400", hint: "" },
  { status: "LOST", label: "Perdu", color: "bg-rose-300", hint: "pas intéressé, sans suite" },
];
const DAY = 86400000;
const since = (d: string | null) => (d ? Math.floor((Date.now() - new Date(d).getTime()) / DAY) : null);
const pct = (a: number, b: number) => (b ? `${Math.round((100 * a) / b)} %` : "—");

export default function PipelineBoard({ rows, stats }: { rows: any[]; stats: { contacted: number; replied: number; interested: number; won: number } }) {
  const router = useRouter();
  const [items, setItems] = useState(rows);
  const [drag, setDrag] = useState<number | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const col = (p: any) => (p.status === "QUALIFIED" ? "TO_CONTACT" : p.status);
  const shown = useMemo(() => items.filter((p) => !q || `${p.company_name} ${p.trade_name ?? ""} ${p.city ?? ""}`.toLowerCase().includes(q.toLowerCase())), [items, q]);

  async function move(id: number, status: string) {
    const prev = items;
    setItems(items.map((p) => (p.id === id ? { ...p, status, contacted_at: status === "CONTACTED" && !p.contacted_at ? new Date().toISOString() : p.contacted_at } : p)));
    const res = await fetch(`/api/local/prospects/${id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ status }) });
    if (!res.ok) { setItems(prev); setErr((await res.json().catch(() => ({}))).error ?? "Refusé"); return; }
    setErr(null); router.refresh();
  }

  const funnel: [string, number, string][] = [
    ["Contactés", stats.contacted, ""], ["Réponses", stats.replied, pct(stats.replied, stats.contacted)],
    ["Intéressés", stats.interested, pct(stats.interested, stats.replied)], ["Clients", stats.won, pct(stats.won, stats.interested)],
  ];
  return (
    <div>
      <div className="mb-10 grid grid-cols-2 gap-x-6 gap-y-6 sm:grid-cols-5">
        {funnel.map(([l, n, r]) => (
          <div key={l} className="min-w-0 border-t border-ink pt-3"><div className="big-number text-4xl">{n}</div>
            <div className="mono mt-2">{l}</div>{r && <div className="mt-0.5 text-xs text-muted">{r} de l'étape précédente</div>}</div>
        ))}
        <div className="col-span-2 min-w-0 border-t border-ink pt-3 sm:col-span-1"><div className="big-number text-4xl text-accent">{pct(stats.won, stats.contacted)}</div>
          <div className="mono mt-2">Taux de transformation</div><div className="mt-0.5 text-xs text-muted">{stats.contacted < 20 ? "échantillon encore trop petit" : "clients / contactés"}</div></div>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filtrer (nom, ville)" className="field w-full sm:w-auto sm:min-w-[240px]" aria-label="Filtrer" />
        <span className="text-xs text-muted">Sur téléphone, utilise le menu en bas de chaque carte.</span>
        {err && <span className="text-sm text-red-700">{err}</span>}
      </div>
      <div className="-mx-4 flex snap-x gap-4 overflow-x-auto px-4 pb-4 sm:mx-0 sm:px-0">
        {PIPELINE.map((c) => {
          const list = shown.filter((p) => col(p) === c.status);
          return (
            <section key={c.status} onDragOver={(e) => { e.preventDefault(); setOver(c.status); }} onDragLeave={() => setOver(null)}
              onDrop={(e) => { e.preventDefault(); setOver(null); if (drag != null) move(drag, c.status); setDrag(null); }}
              className={`flex w-[78vw] max-w-[17rem] shrink-0 snap-start flex-col border-t-2 pt-3 sm:w-64 ${over === c.status ? "border-accent bg-accent/5" : "border-ink"}`}>
              <h2 className="mb-1 flex items-center gap-2 font-mono text-xs font-semibold uppercase tracking-[0.12em]"><span className={`h-2.5 w-2.5 rounded-full ${c.color}`} />{c.label}
                <span className="ml-auto font-mono text-xs font-semibold text-muted">{list.length}</span></h2>
              <div className="mb-2 min-h-[1rem] text-[11px] text-muted">{c.hint}</div>
              <div className="flex max-h-[70vh] flex-col overflow-y-auto border-t border-line">
                {list.map((p) => {
                  const days = since(p.last_contacted_at || p.contacted_at);
                  const stale = p.status === "CONTACTED" && days != null && days >= 7;
                  return (
                    <article key={p.id} draggable onDragStart={() => setDrag(p.id)} onDragEnd={() => setDrag(null)}
                      className={`cursor-grab border-b border-line py-3 text-sm active:cursor-grabbing ${drag === p.id ? "opacity-50" : ""} ${stale ? "bg-amber-50/70 px-2" : ""}`}>
                      <Link href={`/local/${p.id}`} className="block break-words font-bold leading-tight tracking-[-0.01em] hover:text-accent">{p.trade_name || p.company_name}</Link>
                      <div className="text-xs text-muted">{[p.city, siteInfo(p).label].filter(Boolean).join(" · ")}</div>
                      <div className="mt-1 flex flex-wrap gap-2 text-xs">
                        {p.phone && <a href={`tel:${p.phone}`} className="text-accent hover:underline">☎ {p.phone.replace(/(\d{2})(?=\d)/g, "$1 ")}</a>}
                        {p.email && <a href={`mailto:${p.email}`} className="break-all text-accent hover:underline">✉ e-mail</a>}
                        {p.draft_created_at && <span className="text-muted">✎ brouillon</span>}
                      </div>
                      {days != null && p.status !== "TO_CONTACT" && <div className={`mt-1 text-[11px] ${stale ? "font-medium text-amber-800" : "text-muted"}`}>
                        {stale ? `sans réponse depuis ${days} j — à relancer ?` : `contacté il y a ${days} j`}</div>}
                      {p.notes && <div className="mt-1 line-clamp-2 text-xs text-muted">{p.notes}</div>}
                      <select value={col(p)} onChange={(e) => move(p.id, e.target.value)} className="field mt-2 w-full text-xs" aria-label="Déplacer vers">
                        {PIPELINE.map((o) => <option key={o.status} value={o.status}>→ {o.label}</option>)}
                      </select>
                    </article>
                  );
                })}
                {list.length === 0 && <div className="py-6 text-center text-xs text-muted">Glisse une carte ici</div>}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
