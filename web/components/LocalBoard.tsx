"use client";
import { useEffect, useMemo, useState } from "react";
import { LocalRow } from "@/lib/local";
import { BOARD_SORTS as SORTS, BOARD_VIEWS as VIEWS, boardBase, filterBoard } from "@/lib/board";
import { CompanyRow, RowList } from "./LocalUI";
import ExportButton from "./ExportButton";

const PAGE = 40;

export default function LocalBoard({ rows }: { rows: LocalRow[] }) {
  const [view, setView] = useState("all");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("score");
  const [activity, setActivity] = useState("");
  const [contactOnly, setContactOnly] = useState(false);
  const [showIgnored, setShowIgnored] = useState(false);
  const [page, setPage] = useState(0);
  const activities = useMemo(() => [...new Set(rows.map((r) => r.activity_label).filter(Boolean) as string[])].sort(), [rows]);

  const base = useMemo(() => boardBase(rows, showIgnored), [rows, showIgnored]);
  const counts = useMemo(() => Object.fromEntries(VIEWS.map((v) => [v.id, base.filter(v.test).length])), [base]);
  const list = useMemo(() => filterBoard(rows, { view, q, sort, activity, contactOnly, showIgnored }), [rows, view, q, sort, activity, contactOnly, showIgnored]);
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
      {list.length > 0 && <ExportButton params={{ list: "all", view, q, sort, activity, ...(contactOnly ? { contact: "1" } : {}), ...(showIgnored ? { ignored: "1" } : {}) }} />}
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
