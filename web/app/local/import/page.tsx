import Link from "next/link";
import { PageHeader } from "@/components/LocalUI";
import ImportPanel from "@/components/ImportPanel";
import { safeRows } from "@/lib/localdb";
import { parseId } from "@/lib/security";

export const dynamic = "force-dynamic";

const OUTCOME_LABELS: Record<string, string> = {
  CREATED: "Créée", UPDATED: "Complétée", UNCHANGED: "Inchangée", IGNORED: "Ignorée", SKIPPED: "Décochée", ERROR: "Erreur",
};
const when = (v: unknown) => new Date(String(v)).toLocaleString("fr-FR", { timeZone: "Europe/Paris", dateStyle: "short", timeStyle: "short" });

/** Import CSV (aperçu puis confirmation) et journal des imports (issues et fiches seulement : aucune donnée de ligne conservée). */
export default async function ImportPage({ searchParams }: { searchParams: Promise<{ journal?: string }> }) {
  const journalId = parseId((await searchParams).journal);
  const imports = await safeRows<any>(`SELECT id, created_at, filename, total_rows, created_count, updated_count, unchanged_count, ignored_count, error_count
    FROM local_imports ORDER BY id DESC LIMIT 10`);
  const detail = journalId ? await safeRows<any>(`SELECT r.line_no, r.outcome, r.prospect_id, r.reason, r.fields, p.id AS still, COALESCE(p.trade_name, p.company_name) AS name
    FROM local_import_rows r LEFT JOIN local_prospects p ON p.id = r.prospect_id WHERE r.import_id=? ORDER BY r.line_no LIMIT 5000`, [journalId]) : [];
  return (
    <div className="max-w-3xl">
      <PageHeader title="Importer un CSV"
        intro="Ajoute ou complète des fiches depuis un fichier CSV (par exemple un export de l'application). Un aperçu te montre tout avant d'enregistrer quoi que ce soit.">
        <Link href="/local/contact" className="btn-ghost btn-sm">← À contacter</Link>
        <Link href="/local" className="btn-ghost btn-sm">Entreprises</Link>
      </PageHeader>
      <ImportPanel />

      <section className="mb-12">
        <h2 className="section-title">Journal des imports</h2>
        {imports.length === 0 && <p className="text-sm text-muted">Aucun import pour l'instant.</p>}
        {imports.length > 0 && (
          <div className="row-list">
            {imports.map((i) => (
              <div key={i.id} className="row-item flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-3 text-sm">
                <div className="min-w-0">
                  <div className="break-all font-medium">N° {i.id} · {i.filename ?? "import.csv"}</div>
                  <div className="text-xs text-muted">
                    {when(i.created_at)} · {i.total_rows} ligne(s) · {i.created_count} créée(s) · {i.updated_count} complétée(s) · {i.unchanged_count} inchangée(s)
                    · {i.ignored_count} ignorée(s) · {i.error_count} erreur(s)
                  </div>
                </div>
                <Link href={`/local/import?journal=${i.id}`} className="btn-ghost btn-sm">Détail ↗</Link>
              </div>))}
          </div>
        )}
      </section>

      {journalId && (
        <section className="mb-12" id="journal">
          <h2 className="section-title">Import n° {journalId} · détail des lignes</h2>
          {detail.length === 0 && <p className="text-sm text-muted">Import introuvable.</p>}
          <div className="row-list">
            {detail.map((r) => (
              <div key={r.line_no} className="row-item py-3 text-sm">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="font-mono text-xs text-muted">L. {r.line_no}</span>
                  <span className="chip">{OUTCOME_LABELS[r.outcome] ?? r.outcome}</span>
                  {r.still ? <Link href={`/local/${r.prospect_id}`} className="break-words font-medium text-accent underline">{String(r.name ?? `Fiche n° ${r.prospect_id}`)}</Link>
                    : r.prospect_id ? <span className="text-muted">Fiche n° {r.prospect_id} (supprimée depuis)</span> : null}
                </div>
                {r.reason && <p className="mt-1 break-words text-muted">{r.reason}{r.fields && r.outcome === "UPDATED" ? ` : ${r.fields}` : ""}</p>}
              </div>))}
          </div>
        </section>
      )}
    </div>
  );
}
