import { PageHeader } from "@/components/LocalUI";
import PipelineBoard from "@/components/PipelineBoard";
import { LIST_COLUMNS, safeRows } from "@/lib/localdb";

export const dynamic = "force-dynamic";

/** Pipeline commercial (Kanban) : de « à contacter » à « client obtenu ». Chaque déplacement est fait par toi ; rien n'est envoyé automatiquement. */
export default async function Suivi() {
  const rows = await safeRows<any>(
    `SELECT ${LIST_COLUMNS}, p.notes, p.response_status FROM local_prospects p
     WHERE p.do_not_contact = 0 AND p.status <> 'DO_NOT_CONTACT'
       AND (p.status IN ('TO_CONTACT','CONTACTED','REPLIED','INTERESTED','WON','LOST')
            OR (p.status = 'QUALIFIED' AND p.category IN ('TRES_BON','A_CONTACTER') AND p.excluded_reason IS NULL))
     ORDER BY p.prospect_score DESC LIMIT 1500`);
  const [st] = await safeRows<any>(`SELECT COALESCE(SUM(contacted_at IS NOT NULL OR status IN ('CONTACTED','REPLIED','INTERESTED','WON') OR response_status IN ('REPLIED','INTERESTED','NOT_INTERESTED','WON')),0) AS contacted,
      COALESCE(SUM(status IN ('REPLIED','INTERESTED','WON') OR response_status IN ('REPLIED','INTERESTED','NOT_INTERESTED','WON')),0) AS replied,
      COALESCE(SUM(status IN ('INTERESTED','WON')),0) AS interested, COALESCE(SUM(status='WON'),0) AS won FROM local_prospects`);
  const stats = { contacted: Number(st?.contacted ?? 0), replied: Number(st?.replied ?? 0), interested: Number(st?.interested ?? 0), won: Number(st?.won ?? 0) };
  return (
    <div className="max-w-[1400px]">
      <PageHeader title="Suivi" kicker="02 — Pipeline" intro="Où en es-tu avec chaque entreprise, de « à contacter » à « client obtenu ». Glisse une carte d'une colonne à l'autre." />
      <PipelineBoard rows={JSON.parse(JSON.stringify(rows))} stats={stats} />
    </div>
  );
}
