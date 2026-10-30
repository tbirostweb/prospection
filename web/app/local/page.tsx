import Link from "next/link";
import { PageHeader } from "@/components/LocalUI";
import RemindersPanel from "@/components/RemindersPanel";
import LocalBoard from "@/components/LocalBoard";
import { BOARD_SQL, LIST_COLUMNS, safeRows } from "@/lib/localdb";
import { LocalRow } from "@/lib/local";

export const dynamic = "force-dynamic";

/** Toutes les entreprises trouvées par les campagnes, avec recherche et quelques filtres simples. */
export default async function LocalPage({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  const { view } = await searchParams;
  const rows = await safeRows<LocalRow>(`SELECT ${LIST_COLUMNS} ${BOARD_SQL}`);
  return (
    <div className="max-w-4xl">
      <PageHeader title="Entreprises" intro="Toutes les entreprises trouvées par tes campagnes ou importées.">
        <Link href="/local/carte" className="btn-ghost btn-sm">Carte</Link>
        <Link href="/local/import" className="btn-ghost btn-sm">Importer un CSV</Link>
      </PageHeader>
      <RemindersPanel />
      <LocalBoard rows={JSON.parse(JSON.stringify(rows))} initialView={typeof view === "string" ? view : "all"} />
    </div>
  );
}
