import { query } from "@/lib/db";

/** Colonnes d'une ligne de liste (pas les gros champs JSON de l'audit). */
export const LIST_COLUMNS = `p.id, p.siret, p.company_name, p.trade_name, p.activity_key, p.activity_label, p.city, p.postal_code, p.distance_km,
  p.company_created_at, p.website_url, p.website_status, p.website_confidence, p.seo_score, p.performance_score, p.technical_score,
  p.modernization_opportunity, p.lighthouse_performance, p.issues, p.prospect_score, p.score_stage, p.category, p.status, p.is_chain,
  p.excluded_reason, p.do_not_contact, p.phone, p.email, p.email_kind, p.contact_page, p.bodacc, p.discovered_at, p.contacted_at,
  p.commercial_potential_score, p.data_confidence_score, p.website_search_tried, p.website_search_total, p.website_absence_confidence, p.chain_kind,
  p.contact_confidence, p.contact_form, p.seo_opportunity_score, p.pipeline_stage, p.error_category, p.entity_level, p.sibling_count,
  p.latitude, p.longitude, p.new_business_at, p.draft_created_at, p.last_contacted_at, p.buy_signals, p.budget_level, p.followups, p.manager_name`;

export async function getLocalHome(): Promise<{ city?: string; postalCode?: string; defaultRadius?: number }> {
  try {
    const rows = await query<any>("SELECT value_json FROM settings WHERE skey='local_home' LIMIT 1");
    const v = rows[0]?.value_json;
    return typeof v === "string" ? JSON.parse(v) : v ?? {};
  } catch { return {}; }
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
  try { return await query<T>(sql, params); } catch (e) { console.error("[local]", e); return []; }
}
