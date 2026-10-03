import Link from "next/link";
import { CompanyRow, PageHeader, RowList } from "@/components/LocalUI";
import { LIST_COLUMNS, safeRows } from "@/lib/localdb";
import { LocalRow, READY_SQL, bestTime, followupDue, formatPhone } from "@/lib/local";
import FollowupButtons from "@/components/FollowupButtons";

export const dynamic = "force-dynamic";

/** Écran principal : les entreprises prêtes à être contactées (site tranché + au moins un contact). Rien n'est jamais envoyé automatiquement. */
export default async function ContactQueue() {
  const rows = await safeRows<LocalRow>(`
    SELECT ${LIST_COLUMNS} FROM local_prospects p
    WHERE p.do_not_contact = 0 AND p.excluded_reason IS NULL AND p.status <> 'DO_NOT_CONTACT'
      AND (p.status = 'TO_CONTACT' OR (p.status IN ('QUALIFIED','AUDITED','ENRICHED') AND p.category IN ('TRES_BON','A_CONTACTER')))
      AND (p.status = 'TO_CONTACT' OR (${READY_SQL}))
    ORDER BY p.status = 'TO_CONTACT' DESC, p.prospect_score DESC LIMIT 300`);
  const [pending] = await safeRows<any>(`SELECT COUNT(*) AS n FROM local_prospects p WHERE p.category IN ('TRES_BON','A_CONTACTER') AND p.do_not_contact = 0
    AND p.excluded_reason IS NULL AND p.status IN ('QUALIFIED','AUDITED','ENRICHED','DISCOVERED') AND NOT (${READY_SQL})`);
  const contacted = await safeRows<LocalRow>(`SELECT ${LIST_COLUMNS} FROM local_prospects p
    WHERE p.status = 'CONTACTED' AND p.do_not_contact = 0 AND p.contacted_at IS NOT NULL ORDER BY p.contacted_at LIMIT 300`);
  const due = contacted.map((p) => ({ p: JSON.parse(JSON.stringify(p)) as LocalRow, d: followupDue({ status: p.status, contacted_at: p.contacted_at ? String(p.contacted_at) : null, followups: p.followups }) }))
    .filter((x) => x.d);
  return (
    <div className="max-w-3xl">
      <PageHeader title="À contacter" kicker="01 — Aujourd'hui"
        intro={<>Les entreprises dont on sait si elles ont un site et qui ont au moins un moyen de contact. Tu contactes toi-même, puis tu cliques « Marquer contacté ».</>}>
        <Link href="/local/tournee" className="btn-ghost btn-sm">Préparer une tournée ↗</Link>
      </PageHeader>
      {due.length > 0 && (
        <section className="mb-12">
          <h2 className="section-title">À relancer aujourd'hui · {due.length}</h2>
          <div className="row-list">{due.map(({ p, d }) => (
            <div key={p.id} className="row-item flex flex-wrap items-center justify-between gap-3 py-4 text-sm">
              <div className="min-w-0">
                <Link href={`/local/${p.id}`} className="font-display text-lg font-bold uppercase tracking-[-0.01em] hover:text-accent">{(p.trade_name || p.company_name).replace(/\s+/g, " ")}</Link>
                <span className="text-muted"> · {d!.label}</span>
                {p.phone && <> · <a href={`tel:${p.phone}`} className="font-medium text-accent hover:underline">☎ {formatPhone(p.phone)}</a></>}
                <div className="text-xs text-muted">Quand appeler : {bestTime(p.activity_key)}</div>
              </div>
              <FollowupButtons id={p.id} kind={d!.kind} />
            </div>))}</div>
        </section>
      )}
      <h2 className="section-title">Entreprises à contacter · {rows.length}</h2>
      <RowList>
        {rows.map((p) => <CompanyRow key={p.id} p={JSON.parse(JSON.stringify(p))} actions />)}
        {rows.length === 0 && <div className="py-10 text-center text-sm text-muted">
          Personne à contacter pour l'instant. <Link href="/local/campagnes" className="underline">Lance une campagne</Link> : les entreprises arrivent ici au fil des passages (toutes les 15 min).</div>}
      </RowList>
      {Number(pending?.n) > 0 && <p className="mt-6 text-sm text-muted">{pending.n} autre(s) entreprise(s) en cours de vérification (site ou contact) : elles apparaîtront ici une fois prêtes.</p>}
    </div>
  );
}
