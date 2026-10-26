import { query } from "@/lib/db";
import { READY_SQL } from "@/lib/local";
import { AUTO_DEFAULTS, AutoSend } from "@/lib/autosend";

/** Colonnes d'une ligne de liste (pas les gros champs JSON de l'audit). */
export const LIST_COLUMNS = `p.id, p.siret, p.company_name, p.trade_name, p.activity_key, p.activity_label, p.city, p.postal_code, p.distance_km,
  p.company_created_at, p.website_url, p.website_status, p.website_confidence, p.seo_score, p.performance_score, p.technical_score,
  p.modernization_opportunity, p.lighthouse_performance, p.issues, p.prospect_score, p.score_stage, p.category, p.status, p.is_chain,
  p.excluded_reason, p.do_not_contact, p.phone, p.email, p.email_kind, p.contact_page, p.bodacc, p.discovered_at, p.contacted_at,
  p.commercial_potential_score, p.data_confidence_score, p.website_search_tried, p.website_search_total, p.website_absence_confidence, p.chain_kind,
  p.contact_confidence, p.contact_form, p.seo_opportunity_score, p.pipeline_stage, p.error_category, p.entity_level, p.sibling_count,
  p.latitude, p.longitude, p.new_business_at, p.draft_created_at, p.last_contacted_at, p.buy_signals, p.budget_level, p.followups, p.manager_name, p.deep_enriched_at, p.social_links, p.address, p.site_year, p.site_review`;

/** File « À contacter » (page /local/contact et son export CSV) : jamais d'opposition ni d'exclusion, fiche prête ou marquée à contacter. */
export const CONTACT_QUEUE_LIMIT = 300;
export const CONTACT_QUEUE_SQL = `FROM local_prospects p
    WHERE p.do_not_contact = 0 AND p.excluded_reason IS NULL AND p.status <> 'DO_NOT_CONTACT'
      AND (p.status = 'TO_CONTACT' OR (p.status IN ('QUALIFIED','AUDITED','ENRICHED') AND p.category IN ('TRES_BON','A_CONTACTER')))
      AND (p.status = 'TO_CONTACT' OR (${READY_SQL}))
    ORDER BY p.status = 'TO_CONTACT' DESC, p.prospect_score DESC LIMIT ${CONTACT_QUEUE_LIMIT}`;

/** Page « Entreprises » (et son export CSV) : toutes les fiches, filtrées ensuite côté client / par lib/board. */
export const BOARD_LIMIT = 3000;
export const BOARD_SQL = `FROM local_prospects p ORDER BY p.prospect_score DESC, p.id LIMIT ${BOARD_LIMIT}`;

export async function getLocalHome(): Promise<{ city?: string; postalCode?: string; defaultRadius?: number }> {
  try {
    const rows = await query<any>("SELECT value_json FROM settings WHERE skey='local_home' LIMIT 1");
    const v = rows[0]?.value_json;
    return typeof v === "string" ? JSON.parse(v) : v ?? {};
  } catch { return {}; }
}

/** Réglage d'envoi automatique complet (valeurs par défaut pour les clés absentes). */
export async function getAutoSend(): Promise<AutoSend> {
  return { ...AUTO_DEFAULTS, ...(await getSetting<Partial<AutoSend>>("local_auto_send", {})) };
}

export async function getSetting<T>(key: string, fallback: T): Promise<T> {
  try {
    const rows = await query<any>("SELECT value_json FROM settings WHERE skey=? LIMIT 1", [key]);
    const v = rows[0]?.value_json;
    return v == null ? fallback : typeof v === "string" ? JSON.parse(v) : v;
  } catch { return fallback; }
}

/** Une requête qui échoue (table absente avant migration…) ne doit pas faire tomber la page. */
export async function safeRows<T = any>(sql: string, params: any[] = []): Promise<T[]> {
  // Journal minimal : code d'erreur seulement (le texte d'une erreur MySQL peut contenir des valeurs de la requête, donc des données personnelles).
  try { return await query<T>(sql, params); } catch (e: any) { console.error("[local] requête en échec :", e?.code ?? "erreur", e?.errno ?? ""); return []; }
}

/** Google Places (facultatif) : requêtes du mois UTC (comme le worker), plafond écrit par le worker (migration 031, 900 si NULL), prospects interrogés
 *  (provenance `places`), dont fiche avec site (`had_website`), dont site vérifié en base. `ever` = au moins un appel un jour. */
export async function getPlacesStats() {
  const [u] = await safeRows<any>(`SELECT requests FROM local_places_usage WHERE month = DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m')`);
  const [b] = await safeRows<any>(`SELECT budget FROM local_places_usage WHERE budget IS NOT NULL ORDER BY month DESC LIMIT 1`);   // colonne absente avant la migration 031 : [] -> 900
  const [c] = await safeRows<any>(`SELECT COUNT(*) AS asked, COALESCE(SUM(JSON_UNQUOTE(JSON_EXTRACT(provenance, '$.places.had_website')) = 'true'),0) AS had_site,
      COALESCE(SUM(JSON_UNQUOTE(JSON_EXTRACT(provenance, '$.places.had_website')) = 'true' AND website_status IN ('CONFIRMED','PROBABLE')),0) AS verified
      FROM local_prospects WHERE JSON_EXTRACT(provenance, '$.places') IS NOT NULL`);
  const [ever] = await safeRows<any>(`SELECT COALESCE(SUM(requests),0) AS n FROM local_places_usage`);
  const asked = Number(c?.asked ?? 0);
  // mêmes critères que worker/local/siterecheck.py (NO_SITE_FILTER + sans provenance `places`)
  const [t] = await safeRows<any>(`SELECT COUNT(*) AS n FROM local_prospects p
      WHERE p.website_status IN ('NOT_FOUND','UNCERTAIN') AND p.contacted_at IS NULL AND (p.auto_state IS NULL OR p.auto_state = 'queued')
        AND p.do_not_contact = 0 AND p.excluded_reason IS NULL AND p.is_chain = 0 AND p.pipeline_stage = 'DONE'
        AND (p.category IS NULL OR p.category <> 'IGNORER') AND p.status NOT IN ('LOST','WON','DO_NOT_CONTACT','CONTACTED')
        AND JSON_EXTRACT(COALESCE(p.provenance, JSON_OBJECT()), '$.places') IS NULL
        AND EXISTS (SELECT 1 FROM local_prospect_campaigns pc WHERE pc.prospect_id = p.id)`);
  // découverte des campagnes (worker/local/placesdiscovery.py) : fiches rattachées à SIRENE, dont celles qu'aucune autre source n'avait ; résultats ignorés
  const [f] = await safeRows<any>(`SELECT COUNT(*) AS n, COALESCE(SUM(NOT EXISTS (SELECT 1 FROM local_prospect_sources o WHERE o.prospect_id = s.prospect_id
      AND o.source IN ('sirene','sirene_bulk'))),0) AS only FROM local_prospect_sources s WHERE s.source = 'places'`);
  const [qd] = await safeRows<any>(`SELECT COUNT(*) AS queries, COALESCE(SUM(unmatched),0) AS unmatched FROM local_places_queries`);   // table absente avant la migration 032 : []
  const backfill = await getSetting<{ enabled?: boolean }>("local_places_backfill", {});
  return { pending: Number(t?.n ?? 0), backfillOn: backfill?.enabled === true,
     requests: Number(u?.requests ?? 0), budget: Number(b?.budget ?? 900), asked, hadSite: Number(c?.had_site ?? 0), verified: Number(c?.verified ?? 0),
    found: Number(f?.n ?? 0), onlyPlaces: Number(f?.only ?? 0), queries: Number(qd?.queries ?? 0), unmatched: Number(qd?.unmatched ?? 0),
    ever: asked > 0 || Number(ever?.n ?? 0) > 0 };
}
