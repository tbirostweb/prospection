import { PageHeader } from "@/components/LocalUI";
import CampaignPanel from "@/components/CampaignPanel";
import { getLocalHome, getSetting, safeRows } from "@/lib/localdb";
import { Preset } from "@/lib/local";

export const dynamic = "force-dynamic";

/** Campagnes : créer une recherche (ville + rayon + activités), la relancer, la mettre en pause. */
export default async function CampaignsPage() {
  const [campaigns, home, presets] = await Promise.all([
    safeRows<any>(`
      SELECT c.*, COUNT(p.id) AS prospects,
             COALESCE(SUM(p.website_status IN ('CONFIRMED','PROBABLE')),0) AS with_site,
             COALESCE(SUM(p.website_status='NOT_FOUND' AND p.website_absence_confidence >= 0.6),0) AS no_site,
             COALESCE(SUM(p.website_status IS NULL OR p.website_status='UNCERTAIN' OR (p.website_status='NOT_FOUND' AND COALESCE(p.website_absence_confidence,0) < 0.6)),0) AS pending,
             COALESCE(SUM(p.phone IS NOT NULL OR p.email IS NOT NULL OR p.contact_form=1),0) AS contacts,
             COALESCE(SUM(p.category IN ('TRES_BON','A_CONTACTER')),0) AS to_contact
      FROM local_campaigns c LEFT JOIN local_prospect_campaigns lc ON lc.campaign_id = c.id LEFT JOIN local_prospects p ON p.id = lc.prospect_id
      GROUP BY c.id ORDER BY c.id DESC`),
    getLocalHome(),
    getSetting<Preset[]>("local_presets", []),
  ]);
  return (
    <div className="max-w-3xl">
      <PageHeader title="Campagnes" kicker="04 — Recherche"
        intro="Une campagne cherche les entreprises d'une zone (données officielles SIRENE), vérifie si elles ont un site et trouve un moyen de les contacter. Elle avance toute seule, quelques entreprises toutes les 15 minutes." />
      <CampaignPanel campaigns={JSON.parse(JSON.stringify(campaigns))} home={home} presets={presets} />
    </div>
  );
}
