import Link from "next/link";
import { CompanyRow, PageHeader, RowList, StatCard } from "@/components/LocalUI";
import { CONTACT_QUEUE_SQL, LIST_COLUMNS, safeRows } from "@/lib/localdb";
import ExportButton from "@/components/ExportButton";
import { LocalRow, READY_SQL, bestTime, followupDue, formatPhone, splitByTrigger } from "@/lib/local";
import FollowupButtons from "@/components/FollowupButtons";

export const dynamic = "force-dynamic";

function greeting(): string {
  const h = Number(new Intl.DateTimeFormat("fr-FR", { hour: "numeric", hour12: false, timeZone: "Europe/Paris" }).format(new Date()));
  return h >= 18 || h < 5 ? "Bonsoir" : "Bonjour";
}

/** Écran principal : les entreprises prêtes à être contactées (site tranché + au moins un contact). Rien n'est jamais envoyé automatiquement. */
export default async function ContactQueue() {
  const rows = await safeRows<LocalRow>(`SELECT ${LIST_COLUMNS} ${CONTACT_QUEUE_SQL}`);
  const [pending] = await safeRows<any>(`SELECT COUNT(*) AS n FROM local_prospects p WHERE p.category IN ('TRES_BON','A_CONTACTER') AND p.do_not_contact = 0
    AND p.excluded_reason IS NULL AND p.status IN ('QUALIFIED','AUDITED','ENRICHED','DISCOVERED') AND NOT (${READY_SQL})`);
  const [review] = await safeRows<any>(`SELECT COUNT(*) AS n FROM local_prospects p WHERE p.site_review = 'PENDING' AND p.do_not_contact = 0`);
  const contacted = await safeRows<LocalRow>(`SELECT ${LIST_COLUMNS} FROM local_prospects p
    WHERE p.status = 'CONTACTED' AND p.do_not_contact = 0 AND p.contacted_at IS NOT NULL ORDER BY p.contacted_at LIMIT 300`);
  const due = contacted.map((p) => ({ p: JSON.parse(JSON.stringify(p)) as LocalRow, d: followupDue({ status: p.status, contacted_at: p.contacted_at ? String(p.contacted_at) : null, followups: p.followups }) }))
    .filter((x) => x.d);
  const toReview = Number(review?.n ?? 0);
  const { triggered, others } = splitByTrigger(rows);
  return (
    <div className="max-w-4xl">
      <PageHeader title={greeting()} intro="Voici qui contacter aujourd'hui. Tu contactes toi-même, puis tu cliques « Marquer contacté ».">
        <Link href="/local/tournee" className="btn-ghost btn-sm">Préparer une tournée</Link>
        <Link href="/local/import" className="btn-ghost btn-sm">Importer un CSV</Link>
      </PageHeader>

      <div className="stat-grid mb-8 grid-cols-3 !gap-2 sm:!gap-4">
        <StatCard featured label="À contacter" value={rows.length} hint={triggered.length ? `dont ${triggered.length} avec un déclencheur fort`
          : Number(pending?.n) > 0 ? `+ ${pending.n} en cours de vérification` : "prêtes, avec un contact"} />
        <StatCard label="À relancer aujourd'hui" value={due.length} hint={due.length ? "voir ci-dessous" : "rien à relancer"} />
        <StatCard label="Sites à vérifier" value={toReview} hint={toReview ? "à trancher en un clic →" : "rien à vérifier"} href={toReview ? "/local?view=review" : undefined} />
      </div>

      {due.length > 0 && (
        <section className="mb-8">
          <h2 className="section-title">À relancer aujourd'hui</h2>
          <div className="row-list">{due.map(({ p, d }) => (
            <div key={p.id} className="row-item flex flex-wrap items-center justify-between gap-3 py-4 text-sm">
              <div className="min-w-0">
                <Link href={`/local/${p.id}`} className="text-base font-semibold hover:text-accent">{(p.trade_name || p.company_name).replace(/\s+/g, " ")}</Link>
                <span className="text-muted"> · {d!.label}</span>
                {p.phone && <> · <a href={`tel:${p.phone}`} className="font-medium text-accent hover:underline">☎ {formatPhone(p.phone)}</a></>}
                <div className="text-xs text-muted">Quand appeler : {bestTime(p.activity_key)}</div>
              </div>
              <FollowupButtons id={p.id} kind={d!.kind} />
            </div>))}</div>
        </section>
      )}

      {triggered.length > 0 && (
        <section className="mb-8">
          <h2 className="section-title !mb-1">⚡ Avec un déclencheur fort · {triggered.length}</h2>
          <p className="mb-3 text-sm text-muted">Une raison d'acheter maintenant (reprise, ouverture récente sans site, site en panne, changement de nom…) : à contacter en premier.</p>
          <RowList>{triggered.map((p) => <CompanyRow key={p.id} p={JSON.parse(JSON.stringify(p))} actions />)}</RowList>
        </section>
      )}

      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="section-title !mb-0">{triggered.length ? `Les autres entreprises à contacter · ${others.length}` : "Entreprises à contacter"}</h2>
        {rows.length > 0 && <ExportButton params={{ list: "contact" }} />}
      </div>
      <RowList>
        {others.map((p) => <CompanyRow key={p.id} p={JSON.parse(JSON.stringify(p))} actions />)}
        {rows.length > 0 && others.length === 0 && <div className="px-6 py-8 text-center text-sm text-muted">Toutes les entreprises prêtes ont un déclencheur fort : elles sont listées ci-dessus.</div>}
        {rows.length === 0 && <div className="px-6 py-12 text-center text-sm text-muted">
          Personne à contacter pour l'instant. <Link href="/local/campagnes" className="font-medium text-ink underline">Lance une campagne</Link> : les entreprises arrivent ici au fil des passages (toutes les 15 min).</div>}
      </RowList>
    </div>
  );
}
