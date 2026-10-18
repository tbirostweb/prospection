"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { IMPORT_MAX_BYTES } from "@/lib/csvimport-limits";
import type { AnalyzedRow, Kind } from "@/lib/csvimport";
import type { ImportSummary, PreviewResult } from "@/lib/csvimport-db";

// Tout le texte affiché vient de React (échappé) : aucune cellule du fichier n'est jamais interprétée comme HTML.
const KIND_LABEL: Record<Kind, string> = { NEW: "Nouvelle", EXISTING: "Existante", AMBIGUOUS: "Ambiguë", ERROR: "Erreur" };
const KIND_CLASS: Record<Kind, string> = {
  NEW: "border-emerald-200 bg-emerald-50 text-emerald-800", EXISTING: "border-line bg-paper/40 text-ink",
  AMBIGUOUS: "border-amber-200 bg-amber-50 text-amber-900", ERROR: "border-red-200 bg-red-50 text-red-800",
};
type Filter = "all" | Kind | "warn";
type Choice = { action: "link"; id: number } | { action: "create" } | { action: "ignore" };
const PAGE = 100;

function toBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

async function post(url: string, body: unknown): Promise<{ ok: boolean; status: number; data: any }> {
  const res = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), cache: "no-store" });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

/** « Importer un CSV » : choisir un fichier → aperçu (aucune écriture) → résoudre les ambiguïtés → confirmer (tout ou rien). */
export default function ImportPanel() {
  const router = useRouter();
  const [file, setFile] = useState<{ name: string; mime: string; data: string } | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [choices, setChoices] = useState<Record<number, Choice>>({});
  const [skip, setSkip] = useState<Set<number>>(new Set());
  const [filter, setFilter] = useState<Filter>("all");
  const [shown, setShown] = useState(PAGE);
  const [busy, setBusy] = useState<"preview" | "confirm" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<ImportSummary | null>(null);

  function reset() {
    setFile(null); setPreview(null); setChoices({}); setSkip(new Set()); setFilter("all"); setShown(PAGE); setError(null); setSummary(null);
  }

  async function choose(f: File | undefined) {
    reset();
    if (!f) return;
    if (!/\.csv$/i.test(f.name)) { setError("Choisis un fichier .csv"); return; }
    if (f.size > IMPORT_MAX_BYTES) { setError(`Fichier trop volumineux (maximum ${IMPORT_MAX_BYTES / 1024 / 1024} Mo)`); return; }
    setBusy("preview");
    try {
      const payload = { name: f.name, mime: f.type, data: toBase64(await f.arrayBuffer()) };
      const r = await post("/api/local/import/preview", { filename: payload.name, mime: payload.mime, data: payload.data });
      if (!r.ok) { setError(r.status === 401 ? "Session expirée : reconnecte-toi." : r.data?.error ?? "Aperçu impossible pour le moment."); return; }
      setFile(payload);
      setPreview(r.data);
    } catch {
      setError("Aperçu impossible pour le moment.");
    } finally {
      setBusy(null);
    }
  }

  const rows = preview?.rows ?? [];
  const unresolved = rows.filter((r) => r.kind === "AMBIGUOUS" && !choices[r.line]).length;
  const willApply = rows.filter((r) => !skip.has(r.line) && (r.kind === "NEW" || (r.kind === "EXISTING" && r.changes.length)
    || (r.kind === "AMBIGUOUS" && choices[r.line] && choices[r.line].action !== "ignore"))).length;
  const visible = useMemo(() => rows.filter((r) => filter === "all" || (filter === "warn" ? r.warnings.length > 0 : r.kind === filter)), [rows, filter]);

  async function confirm() {
    if (!file || !preview || unresolved) return;
    setBusy("confirm");
    setError(null);
    try {
      const r = await post("/api/local/import/confirm", { filename: file.name, mime: file.mime, data: file.data, token: preview.token, resolutions: choices, skip: [...skip] });
      if (!r.ok) { setError(r.status === 401 ? "Session expirée : reconnecte-toi." : r.data?.error ?? "Import annulé : aucune modification enregistrée."); return; }
      setSummary(r.data);
      setPreview(null);
      router.refresh();
    } catch {
      setError("Import annulé : aucune modification enregistrée.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="mb-12">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <label className={`btn-ghost min-h-[44px] w-full cursor-pointer sm:w-auto ${busy ? "pointer-events-none opacity-50" : ""}`}>
          <input type="file" accept=".csv,text/csv" className="sr-only" disabled={!!busy}
            onChange={(e) => { choose(e.target.files?.[0]); e.target.value = ""; }} />
          {busy === "preview" ? "Analyse en cours…" : file ? "Choisir un autre fichier ↗" : "Choisir un fichier CSV ↗"}
        </label>
        <span className="w-full text-xs text-muted">
          CSV UTF-8 (export de l'application), « ; » ou « , », {IMPORT_MAX_BYTES / 1024 / 1024} Mo et 5 000 lignes au plus. L'aperçu n'enregistre rien.
        </span>
      </div>

      {error && <p role="alert" className="mt-4 border-2 border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      {summary && (
        <div className="mt-6" aria-live="polite">
          <h2 className="section-title">Import terminé</h2>
          <div className="stat-grid grid-cols-2 sm:grid-cols-5">
            {([["Créées", summary.created], ["Complétées", summary.updated], ["Inchangées", summary.unchanged], ["Ignorées", summary.ignored], ["Erreurs", summary.errors]] as [string, number][])
              .map(([l, n]) => <div key={l} className="stat-block"><div className="big-number text-3xl">{n}</div><div className="mono mt-2">{l}</div></div>)}
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link href={`/local/import?journal=${summary.importId}`} className="btn-ghost btn-sm">Voir le journal ↗</Link>
            <button type="button" onClick={reset} className="btn-ghost btn-sm">Nouvel import</button>
          </div>
        </div>
      )}

      {preview && (
        <div className="mt-6">
          <h2 className="section-title break-all">Aperçu · {preview.filename}</h2>
          <div className="stat-grid grid-cols-2 sm:grid-cols-4">
            {([["Nouvelles", preview.counts.new, "NEW"], ["Existantes", preview.counts.existing, "EXISTING"], ["Ambiguës", preview.counts.ambiguous, "AMBIGUOUS"],
              ["Erreurs", preview.counts.errors, "ERROR"]] as [string, number, Kind][]).map(([l, n, k]) => (
              <button key={k} type="button" onClick={() => { setFilter(filter === k ? "all" : k); setShown(PAGE); }} aria-pressed={filter === k}
                className={`stat-block min-h-[44px] text-left ${filter === k ? "stat-block-accent" : ""}`}>
                <div className="big-number text-3xl">{n}</div><div className="mono mt-2">{l}</div>
              </button>))}
          </div>
          <ul className="mt-4 space-y-1 text-sm text-muted">
            <li>{preview.counts.total} ligne(s) · séparateur « {preview.separator} » · {preview.counts.changes} fiche(s) existante(s) à compléter (champs vides seulement).</li>
            {!preview.hasSiret && <li>Fichier sans colonne SIRET : rapprochement par SIREN.</li>}
            {preview.previous && <li className="text-amber-900">Ce fichier a déjà été importé (import n° {preview.previous.id}) : le rejouer ne crée aucun doublon.</li>}
            {preview.headerWarnings.map((w) => <li key={w}>{w}</li>)}
            <li>Le statut, les notes, les brouillons et les dates déjà enregistrés ne sont jamais modifiés ; aucune recherche de site n'est lancée.</li>
          </ul>

          <div className="mt-6 flex flex-wrap gap-2" role="group" aria-label="Filtrer les lignes">
            {([["all", "Toutes"], ["AMBIGUOUS", "Ambiguës"], ["ERROR", "Erreurs"], ["warn", "Avertissements"], ["NEW", "Nouvelles"], ["EXISTING", "Existantes"]] as [Filter, string][])
              .map(([k, l]) => <button key={k} type="button" onClick={() => { setFilter(k); setShown(PAGE); }} className={`pill ${filter === k ? "pill-on" : ""}`} aria-pressed={filter === k}>{l}</button>)}
          </div>

          <div className="row-list mt-4">
            {visible.slice(0, shown).map((r) => (
              <ImportLine key={r.line} r={r} choice={choices[r.line]} skipped={skip.has(r.line)}
                onChoice={(c) => setChoices({ ...choices, [r.line]: c })}
                onSkip={(v) => { const s = new Set(skip); if (v) s.add(r.line); else s.delete(r.line); setSkip(s); }} />))}
            {!visible.length && <p className="py-6 text-sm text-muted">Aucune ligne dans ce filtre.</p>}
          </div>
          {visible.length > shown && (
            <button type="button" onClick={() => setShown(shown + PAGE)} className="btn-ghost btn-sm mt-4">Afficher {Math.min(PAGE, visible.length - shown)} de plus</button>)}

          <div className="mt-8 flex flex-wrap items-center gap-x-4 gap-y-2">
            <button type="button" onClick={confirm} disabled={!!busy || unresolved > 0 || willApply === 0} className="btn-primary w-full sm:w-auto">
              {busy === "confirm" ? "Import en cours…" : "Confirmer l'import"}
            </button>
            <span className="text-sm text-muted" aria-live="polite">
              {unresolved > 0 ? `${unresolved} ambiguïté(s) à résoudre avant de confirmer.` : willApply === 0 ? "Rien à importer." : `${willApply} ligne(s) seront créées ou complétées.`}
            </span>
          </div>
        </div>
      )}
    </section>
  );
}

function ImportLine({ r, choice, skipped, onChoice, onSkip }: {
  r: AnalyzedRow; choice?: Choice; skipped: boolean; onChoice: (c: Choice) => void; onSkip: (v: boolean) => void;
}) {
  const applicable = r.kind === "NEW" || (r.kind === "EXISTING" && r.changes.length > 0);
  const group = `amb-${r.line}`;
  return (
    <div className={`row-item py-4 text-sm ${skipped ? "opacity-60" : ""}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="font-mono text-xs text-muted">L. {r.line}</span>
        <span className={`border px-2 py-0.5 font-mono text-[11px] font-medium uppercase tracking-wide ${KIND_CLASS[r.kind]}`}>{KIND_LABEL[r.kind]}</span>
        <span className="min-w-0 break-words font-display text-lg font-bold uppercase leading-tight">{r.name || "Sans nom"}</span>
      </div>
      <div className="mt-1 break-all font-mono text-xs uppercase tracking-wide text-muted">
        {[r.city, r.siret ? `SIRET ${r.siret}` : r.siren ? `SIREN ${r.siren}` : null, r.email, r.phone].filter(Boolean).join(" · ")}
      </div>
      <p className="mt-2 break-words">{r.reason}{r.changes.length > 0 && r.kind === "EXISTING" ? ` : ${r.changes.join(", ")}` : ""}</p>
      {r.matchId && r.kind === "EXISTING" && <Link href={`/local/${r.matchId}`} className="mt-1 inline-flex min-h-[44px] items-center text-xs font-medium text-accent underline">Ouvrir la fiche n° {r.matchId} ↗</Link>}
      {r.warnings.length > 0 && <ul className="mt-2 space-y-1">{r.warnings.map((w) => <li key={w} className="break-words text-xs text-amber-900">⚠ {w}</li>)}</ul>}
      {applicable && (
        <label className="check mt-1 text-sm">
          <input type="checkbox" checked={!skipped} onChange={(e) => onSkip(!e.target.checked)} />
          Importer cette ligne
        </label>
      )}
      {r.kind === "AMBIGUOUS" && (
        <fieldset className="mt-3 border-2 border-amber-300 p-3">
          <legend className="mono px-1 !text-ink">Que faire de cette ligne ?</legend>
          {r.candidates.map((c) => (
            <label key={c.id} className="check text-sm">
              <input type="radio" name={group} checked={choice?.action === "link" && choice.id === c.id} onChange={() => onChoice({ action: "link", id: c.id })} />
              <span className="min-w-0 break-words">Compléter la fiche n° {c.id} — {c.name}{c.city ? `, ${c.city}` : ""}{c.siret ? ` (SIRET ${c.siret})` : " (sans SIRET)"}{c.address ? ` · ${c.address}` : ""}</span>
            </label>))}
          <label className="check text-sm">
            <input type="radio" name={group} checked={choice?.action === "create"} onChange={() => onChoice({ action: "create" })} />
            Créer un nouvel établissement
          </label>
          <label className="check text-sm">
            <input type="radio" name={group} checked={choice?.action === "ignore"} onChange={() => onChoice({ action: "ignore" })} />
            Ignorer cette ligne
          </label>
        </fieldset>
      )}
    </div>
  );
}
