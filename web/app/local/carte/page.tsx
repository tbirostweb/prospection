import Link from "next/link";
import { PageHeader } from "@/components/LocalUI";
import MapClient from "@/components/MapClient";
import { LIST_COLUMNS, getLocalHome, getSetting, safeRows } from "@/lib/localdb";
import { LocalRow, Preset } from "@/lib/local";

export const dynamic = "force-dynamic";

/** Carte des prospects : zone et rayon au choix, pastilles « où j'en suis », filtres, actions rapides, campagne lancée depuis un point. */
export default async function Carte() {
  const [rows, campaigns, home, presets] = await Promise.all([
    safeRows<LocalRow>(`SELECT ${LIST_COLUMNS} FROM local_prospects p WHERE p.latitude IS NOT NULL AND p.excluded_reason IS NULL ORDER BY p.prospect_score DESC LIMIT 6000`),
    safeRows<any>("SELECT id, name, latitude, longitude, radius_km FROM local_campaigns WHERE latitude IS NOT NULL ORDER BY id DESC"),
    getLocalHome(),
    getSetting<Preset[]>("local_presets", []),
  ]);
  return (
    <div className="max-w-[1400px]">
      <PageHeader title="Carte" kicker="03 — Entreprises" intro="Les entreprises sur la carte. Tu peux aussi lancer une campagne depuis un point.">
        <Link href="/local" className="btn-ghost btn-sm">← Liste des entreprises</Link>
      </PageHeader>
      <MapClient rows={JSON.parse(JSON.stringify(rows))} campaigns={JSON.parse(JSON.stringify(campaigns))} presets={presets} defaultRadius={Number(home.defaultRadius) || 10} />
    </div>
  );
}
