"use client";
import { useEffect, useMemo, useState } from "react";
import { LocalRow, simpleStatus, siteInfo } from "@/lib/local";
import { CompanyRow, RowList } from "./LocalUI";

const km = (p: LocalRow) => (p.distance_km == null ? 999 : Number(p.distance_km));
const hasContact = (p: LocalRow) => !!(p.email || p.phone || p.contact_form || p.contact_page);
const PAGE = 40;

const VIEWS: { id: string; label: string; test: (p: LocalRow) => boolean }[] = [
  { id: "all", label: "Toutes", test: () => true },
  { id: "tocontact", label: "À contacter", test: (p) => simpleStatus(p) === "À contacter" },
  { id: "nosite", label: "Pas de site trouvé", test: (p) => siteInfo(p).kind === "no" },
  { id: "down", label: "Site en panne", test: (p) => siteInfo(p).kind === "down" },
  { id: "site", label: "A un site", test: (p) => siteInfo(p).kind === "yes" },
  { id: "worked", label: "Déjà contactées", test: (p) => ["CONTACTED", "REPLIED", "INTERESTED", "WON", "LOST"].includes(p.status) },
];
const SORTS: [string, string, (a: LocalRow, b: LocalRow) => number][] = [
  ["score", "Les plus intéressantes d'abord", (a, b) => (b.prospect_score ?? -1) - (a.prospect_score ?? -1)],
  ["distance", "Les plus proches d'abord", (a, b) => km(a) - km(b)],
  ["name", "Par nom", (a, b) => (a.trade_name || a.company_name).localeCompare(b.trade_name || b.company_name, "fr")],
];

export default function LocalBoard({ rows }: { rows: LocalRow[] }) {
  const [view, setView] = useState("all");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("score");
  const [activity, setActivity] = useState("");
  const [contactOnly, setContactOnly] = useState(false);
  const [showIgnored, setShowIgnored] = useState(false);
  const [page, setPage] = useState(0);
  const activities = useMemo(() => [...new Set(rows.map((r) => r.activity_label).filter(Boolean) as string[])].sort(), [rows]);

  const base = useMemo(() => rows.filter((p) => showIgnored || (p.category !== "IGNORER" && !p.do_not_contact && !p.excluded_reason && !p.is_chain)), [rows, showIgnored]);
  const counts = useMemo(() => Object.fromEntries(VIEWS.map((v) => [v.id, base.filter(v.test).length])), [base]);
  const list = useMemo(() => {
    const v = VIEWS.find((x) => x.id === view) ?? VIEWS[0];
    const needle = q.trim().toLowerCase();
    const cmp = (SORTS.find((s) => s[0] === sort) ?? SORTS[0])[2];
    return base.filter(v.test)
      .filter((p) => !activity || p.activity_label === activity)
      .filter((p) => !contactOnly || hasContact(p))
      .filter((p) => !needle || `${p.company_name} ${p.trade_name ?? ""} ${p.city ?? ""} ${p.activity_label ?? ""}`.toLowerCase().includes(needle)).sort(cmp);
  }, [base, view, q, sort, activity, contactOnly]);
  useEffect(() => setPage(0), [view, q, sort, activity, contactOnly, showIgnored]);
  const pages = Math.max(1, Math.ceil(list.length / PAGE));
  const shown = list.slice(page * PAGE, (page + 1) * PAGE);

  return (
    <div>
      <div className="mb-6 flex flex-wrap gap-2">
        {VIEWS.map((v) => (
          <button key={v.id} onClick={() => setView(v.id)}
            aria-pressed={view === v.id} className={`pill ${view === v.id ? "pill-on" : ""}`}>
            {v.label} <span className={`font-mono ${view === v.id ? "opacity-70" : "text-muted"}`}>{counts[v.id]}</span>
          </button>
        ))}
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Rechercher (nom, ville, activité…)" className="field w-full sm:w-auto sm:min-w-[220px] sm:flex-1" aria-label="Recherche" />
        <select value={activity} onChange={(e) => setActivity(e.target.value)} className="field w-full sm:w-auto" aria-label="Activité">
          <option value="">Toutes les activités</option>{activities.map((a) => <option key={a}>{a}</option>)}</select>
        <select value={sort} onChange={(e) => setSort(e.target.value)} className="field w-full sm:w-auto" aria-label="Tri">
          {SORTS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select>
      </div>
      <div className="mb-8 flex flex-wrap gap-x-6 text-sm">
        <label className="check"><input type="checkbox" checked={contactOnly} onChange={(e) => setContactOnly(e.target.checked)} />
          Avec un moyen de contact</label>
        <label className="check"><input type="checkbox" checked={showIgnored} onChange={(e) => setShowIgnored(e.target.checked)} />
          Afficher aussi les entreprises écartées (chaînes, exclues, sans intérêt)</label>
      </div>
      <div className="mono mb-3">{list.length} entreprise{list.length > 1 ? "s" : ""}{pages > 1 ? ` · page ${page + 1}/${pages}` : ""}</div>
      <RowList>
        {shown.map((p) => <CompanyRow key={p.id} p={p} />)}
        {list.length === 0 && (
          <div className="py-10 text-center text-sm text-muted">
            {rows.length === 0 ? "Aucune entreprise pour l'instant. Lance une campagne : quelques entreprises sont traitées toutes les 15 minutes."
              : "Aucune entreprise ne correspond à cette recherche."}
          </div>
        )}
      </RowList>
      {pages > 1 && (
        <div className="mt-8 flex items-center justify-center gap-3">
          <button disabled={page === 0} onClick={() => setPage(page - 1)} className="btn-ghost btn-sm disabled:opacity-40">← Précédent</button>
          <span className="mono">{page + 1} / {pages}</span>
          <button disabled={page >= pages - 1} onClick={() => setPage(page + 1)} className="btn-ghost btn-sm disabled:opacity-40">Suivant →</button>
        </div>
      )}
    </div>
  );
}
