import Link from "next/link";
import { PageHeader, RowList, StatCard } from "@/components/LocalUI";
import CallRow, { CallItem } from "@/components/CallRow";
import { CALL_QUEUE_SQL, parisDay, safeRows } from "@/lib/localdb";
import { CALL_MAX_ATTEMPTS, addDays, callScript } from "@/lib/calls";
import { ANGLE_LABELS, ficheAngle } from "@/lib/complete";
import { bestTime, formatPhone } from "@/lib/local";
import { shortActivity } from "@/lib/activity";

export const dynamic = "force-dynamic";

/** Fiches sans e-mail utilisable mais avec un téléphone : à appeler par toi, avec un argumentaire de 3 lignes selon le métier et l'angle. */
export default async function CallQueue() {
  const today = parisDay();
  const rows = await safeRows<any>(`SELECT p.* ${CALL_QUEUE_SQL}`, [today]);
  const [waiting] = await safeRows<any>(`SELECT COUNT(*) AS n FROM local_prospects p WHERE p.do_not_contact = 0 AND p.contacted_at IS NULL AND p.call_attempts BETWEEN 1 AND ${CALL_MAX_ATTEMPTS - 1}
    AND p.remind_at > ? AND TRIM(COALESCE(p.phone, '')) <> ''`, [today]);
  const items: CallItem[] = rows.map((p) => {
    const angle = ficheAngle(JSON.parse(JSON.stringify(p)));
    return { id: p.id, name: String(p.trade_name || p.company_name).replace(/\s+/g, " "), city: p.city ?? null, activity: shortActivity(p.activity_label),
      phone: String(p.phone), phoneLabel: formatPhone(String(p.phone)), angle, angleLabel: ANGLE_LABELS[angle].split(" (")[0], score: p.prospect_score != null ? Number(p.prospect_score) : null,
      attempts: Number(p.call_attempts ?? 0), script: callScript(p.activity_label, angle), bestTime: bestTime(p.activity_key), tomorrow: addDays(today, 1) };
  });
  return (
    <div className="max-w-4xl">
      <PageHeader title="À appeler" intro="Entreprises jamais contactées, avec un téléphone et sans e-mail utilisable, les meilleurs scores d'abord. Tu appelles toi-même, puis tu notes le résultat.">
        <Link href="/local/contact" className="btn-ghost btn-sm">← À contacter</Link>
      </PageHeader>
      <div className="stat-grid mb-8 grid-cols-2 !gap-2 sm:!gap-4">
        <StatCard featured label="À appeler" value={items.length} hint={items.length ? "triées par score" : "rien à appeler"} />
        <StatCard label="Rappel prévu" value={Number(waiting?.n ?? 0)} hint={`pas de réponse : retour dans 2 jours, ${CALL_MAX_ATTEMPTS} essais au plus`} />
      </div>
      <RowList>
        {items.map((p) => <CallRow key={p.id} p={p} />)}
        {items.length === 0 && <div className="px-6 py-12 text-center text-sm text-muted">Personne à appeler pour l'instant.</div>}
      </RowList>
    </div>
  );
}
