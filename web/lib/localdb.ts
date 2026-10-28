import { query } from "@/lib/db";
import { READY_SQL } from "@/lib/local";
import { AUTO_DEFAULTS, AutoSend } from "@/lib/autosend";
import { CONTACT_REASONS, ContactReason, SITE_REASONS, SiteReason, qualitySql } from "@/lib/complete";
import { DAY_RUNS_SQL, EnrichRun, LAST_FULL_RUN_SQL, LAST_RUN_SQL, parseSteps } from "@/lib/enrich";

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
      AND (p.status = 'TO_CONTACT' OR (p.status IN ('QUALIFIED','AUDITED','ENRICHED') AND p.category IN ('TRES_BON','A_CONTACTER'))
        OR (p.status IN ('QUALIFIED','AUDITED','ENRICHED','DISCOVERED') AND p.website_status IN ('CONFIRMED','PROBABLE','UNREACHABLE')
            AND p.is_chain = 0 AND p.contacted_at IS NULL AND COALESCE(p.category, '') <> 'IGNORER'))
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
        AND JSON_EXTRACT(COALESCE(p.provenance, JSON_OBJECT()), '$.places') IS NULL`);
  // découverte des campagnes (worker/local/placesdiscovery.py) : fiches rattachées à SIRENE, dont celles qu'aucune autre source n'avait ; résultats ignorés
  const [f] = await safeRows<any>(`SELECT COUNT(*) AS n, COALESCE(SUM(NOT EXISTS (SELECT 1 FROM local_prospect_sources o WHERE o.prospect_id = s.prospect_id
      AND o.source IN ('sirene','sirene_bulk'))),0) AS only FROM local_prospect_sources s WHERE s.source = 'places'`);
  const [qd] = await safeRows<any>(`SELECT COUNT(*) AS queries, COALESCE(SUM(unmatched),0) AS unmatched FROM local_places_queries`);   // table absente avant la migration 032 : []
  // site de la fiche Google : verdict de la vérification (provenance `places.site_checked`, worker/local/siterecheck.py places_site_check)
  const HAD = `JSON_UNQUOTE(JSON_EXTRACT(p.provenance, '$.places.had_website')) = 'true' AND COALESCE(p.website_status, '') NOT IN ('CONFIRMED','PROBABLE')`;
  const CHECK = `JSON_UNQUOTE(JSON_EXTRACT(p.provenance, '$.places.site_checked'))`;
  const reasons = (await safeRows<any>(`SELECT ${CHECK} AS reason, COUNT(*) AS n FROM local_prospects p
      WHERE ${HAD} AND JSON_EXTRACT(p.provenance, '$.places.site_checked_at') IS NOT NULL GROUP BY reason ORDER BY n DESC`))
    .map((r) => ({ reason: String(r.reason ?? ""), n: Number(r.n ?? 0) }));
  // restant à vérifier : mêmes critères que le passage du worker (NO_SITE_FILTER + LOST_SITE), le reste est hors file (contactée, écartée…)
  const [left] = await safeRows<any>(`SELECT COUNT(*) AS n, COALESCE(SUM(p.website_status IN ('NOT_FOUND','UNCERTAIN') AND p.contacted_at IS NULL
        AND (p.auto_state IS NULL OR p.auto_state = 'queued') AND p.do_not_contact = 0 AND p.excluded_reason IS NULL AND p.is_chain = 0
        AND p.pipeline_stage = 'DONE' AND (p.category IS NULL OR p.category <> 'IGNORER') AND p.status NOT IN ('LOST','WON','DO_NOT_CONTACT','CONTACTED')
        AND JSON_TYPE(JSON_EXTRACT(p.provenance, '$.places.place_id')) = 'STRING'),0) AS queued
      FROM local_prospects p WHERE ${HAD} AND JSON_EXTRACT(p.provenance, '$.places.site_checked_at') IS NULL`);
  const backfill = await getSetting<{ enabled?: boolean }>("local_places_backfill", {});
  return { pending: Number(t?.n ?? 0), backfillOn: backfill?.enabled === true,
    siteRejected: reasons.reduce((a, r) => a + r.n, 0), siteReasons: reasons, siteToCheck: Number(left?.queued ?? 0),
    siteOutOfQueue: Number(left?.n ?? 0) - Number(left?.queued ?? 0),
     requests: Number(u?.requests ?? 0), budget: Number(b?.budget ?? 900), asked, hadSite: Number(c?.had_site ?? 0), verified: Number(c?.verified ?? 0),
    found: Number(f?.n ?? 0), onlyPlaces: Number(f?.only ?? 0), queries: Number(qd?.queries ?? 0), unmatched: Number(qd?.unmatched ?? 0),
    ever: asked > 0 || Number(ever?.n ?? 0) > 0 };
}

/** Statistiques → « Qualité de la liste » : fiches à prospecter (actives, indépendantes, jamais contactées), dont complètes (lib/complete.ts) avec e-mail
 *  ou téléphone seul, et ce qui manque aux autres (une fiche peut cumuler). Même réglage personal_emails que l'envoi automatique. */
export async function getQualityStats() {
  const auto = await getAutoSend();
  const [q] = await safeRows<any>(qualitySql(auto.personal_emails));
  const n = (k: string) => Number(q?.[k] ?? 0);
  return { total: n("total"), email: n("complete_email"), phone: n("complete_phone"), closed: n("closed"),
    missing: { processing: n("processing"), noMetier: n("no_metier"), siteTodo: n("site_todo"), noContact: n("no_contact"), emailInvalid: n("email_invalid") },
    // raisons (lib/complete.ts siteReason / contactReason), dans l'ordre d'affichage, seulement celles qui comptent
    siteReasons: (Object.keys(SITE_REASONS) as SiteReason[]).map((c) => ({ code: c, label: SITE_REASONS[c], n: n(`site_${c}`) })).filter((r) => r.n > 0),
    contactReasons: (["none", "form", "refused"] as ContactReason[]).map((c) => ({ code: c, label: CONTACT_REASONS[c], n: n(`contact_${c}`) })).filter((r) => r.n > 0),
    processing: { campaign: n("processing_campaign"), abandoned: n("processing_abandoned"), imported: n("processing_imported") } };
}

/** Statistiques → « Sites retirés par la vérification d'identité » (provenance `site_identity`, worker/local/enrich.py site_identity et
 *  outbox.presend_identity) : autre entreprise, site de groupe, maintenance / certificat invalide, aucune preuve ; fiches contactées seulement
 *  notées. `pending` : sites retenus pas encore relus avec cette version de la règle (sitefinder.IDENTITY_VERSION). */
export const IDENTITY_VERSION = 1;
export async function getIdentityStats() {
  const R = `JSON_UNQUOTE(JSON_EXTRACT(p.provenance, '$.site_identity.result'))`;
  const rows = await safeRows<any>(`SELECT ${R} AS result, COUNT(*) AS n,
      COALESCE(SUM(JSON_UNQUOTE(JSON_EXTRACT(p.provenance, '$.site_identity.contacted')) = 'true'),0) AS contacted
      FROM local_prospects p WHERE ${R} IS NOT NULL AND ${R} NOT IN ('ok','unreachable','error') GROUP BY result ORDER BY n DESC`);
  const [left] = await safeRows<any>(`SELECT COUNT(*) AS n FROM local_prospects p WHERE p.website_status IN ('CONFIRMED','PROBABLE')
      AND p.website_url IS NOT NULL AND COALESCE(p.site_review, '') <> 'CONFIRMED'
      AND COALESCE(CAST(JSON_UNQUOTE(JSON_EXTRACT(p.provenance, '$.site_identity.version')) AS UNSIGNED), 0) < ${IDENTITY_VERSION}`);
  const reasons = rows.map((r) => ({ result: String(r.result ?? ""), n: Number(r.n ?? 0), contacted: Number(r.contacted ?? 0) }));
  return { reasons, total: reasons.reduce((a, r) => a + r.n, 0), contacted: reasons.reduce((a, r) => a + r.contacted, 0), pending: Number(left?.n ?? 0) };
}

/** Statistiques → « Dernier passage d'enrichissement » (worker/local/enrich.py, table local_enrich_runs : absente avant la migration 033 → null). */
export async function getEnrichStats() {
  const run = (r: any): EnrichRun | null => r ? { started_at: r.started_at, duration_ms: Number(r.duration_ms ?? 0), mode: String(r.mode ?? ""),
    stopped: r.stopped ?? null, steps: parseSteps(r.steps) } : null;
  const [last] = await safeRows<any>(LAST_RUN_SQL);
  const [full] = await safeRows<any>(LAST_FULL_RUN_SQL);
  const [day] = await safeRows<any>(DAY_RUNS_SQL);
  return { last: run(last), lastFull: run(full), day: { runs: Number(day?.runs ?? 0), skipped: Number(day?.skipped ?? 0), locked: Number(day?.locked ?? 0) } };
}
