import Link from "next/link";
import { PageHeader } from "@/components/LocalUI";
import LocalBoard from "@/components/LocalBoard";
import { LIST_COLUMNS, safeRows } from "@/lib/localdb";
import { LocalRow } from "@/lib/local";

export const dynamic = "force-dynamic";

/** Toutes les entreprises trouvées par les campagnes, avec recherche et quelques filtres simples. */
export default async function LocalPage() {
  const rows = await safeRows<LocalRow>(`SELECT ${LIST_COLUMNS} FROM local_prospects p ORDER BY p.prospect_score DESC, p.id LIMIT 3000`);
  return (
    <div className="max-w-3xl">
      <PageHeader title="Entreprises" kicker="03 — Toutes les fiches"
        intro="Toutes les entreprises trouvées par tes campagnes. Elles n'ont rien demandé : ce sont des prospects à froid.">
        <Link href="/local/carte" className="btn-ghost btn-sm">Voir sur la carte →</Link>
        <Link href="/local/campagnes" className="btn-ghost btn-sm">+ Nouvelle campagne</Link>
      </PageHeader>
      <LocalBoard rows={JSON.parse(JSON.stringify(rows))} />
    </div>
  );
}
