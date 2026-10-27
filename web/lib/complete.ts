/** « Fiche complète » : LE critère unique de qualité d'une fiche — miroir exact de worker/worker/local/complete.py (mêmes cas de test :
 *  worker/tests/fixtures/fiche_complete_cases.json). Seule une fiche complète AVEC e-mail se met en file d'envoi automatique ; une fiche complète
 *  « téléphone » se contacte à la main ; les autres attendent que l'app les complète.
 *  1. entreprise active et indépendante (ni « ne plus contacter », ni écartée, ni chaîne, ni « Ignorer », pas de radiation / procédure / fermeture BODACC) ;
 *  2. traitement terminé ; 3. métier connu (activité issue du code NAF, jamais devinée du nom) ;
 *  4. site tranché : CONFIRMED / PROBABLE (hors site importé à vérifier), ou NOT_FOUND après recherche complète (recherche moteur faite, revérification
 *     `site_recheck` faite, site de la fiche Google vérifié s'il y en avait un, aucun doute laissé par la vérification d'avant l'envoi), ou
 *     UNREACHABLE encore injoignable après UNREACHABLE_MAX_TRIES nouvelles tentatives (site en panne) ; sinon une raison (siteReason, SITE_REASONS) ;
 *  5. contact fiable : e-mail GENERIC_BUSINESS / PERSONAL_BUSINESS (UNCERTAIN et messageries gratuites seulement avec le réglage personal_emails),
 *     jamais une adresse revenue ou sans serveur de messagerie (auto_state 'bounced') ; à défaut un téléphone (fiche complète « téléphone ») ;
 *     sinon une raison (contactReason, CONTACT_REASONS). */
import { FREEMAIL } from "./autosend";
import { ABSENCE_MIN } from "./local";

export type MissingCode = "entreprise" | "traitement" | "metier" | "site" | "email_invalide" | "contact";
export const MISSING_LABELS: Record<MissingCode, string> = {
  entreprise: "entreprise écartée, chaîne, fermée ou « ne plus contacter »",
  traitement: "traitement pas terminé",
  metier: "métier inconnu (activité non classée)",
  site: "site à trancher",
  email_invalide: "e-mail invalide",
  contact: "aucun contact fiable",
};
export type SiteReason = "review" | "search" | "recheck" | "places" | "presend" | "uncertain" | "uncertain_user" | "unreachable";
/** Pourquoi le site est encore à trancher (mêmes codes et libellés que SITE_REASONS de complete.py), dans l'ordre d'affichage. */
export const SITE_REASONS: Record<SiteReason, string> = {
  review: "site importé à vérifier par toi",
  search: "recherche moteur pas faite ou trop maigre",
  recheck: "revérification pas encore faite",
  places: "site de la fiche Google pas vérifié",
  presend: "doute à la vérification d'avant l'envoi",
  uncertain: "site incertain, revérification à faire",
  uncertain_user: "site incertain après revérification : à trancher par toi",
  unreachable: "site injoignable, nouvel essai prévu",
};
export type ContactReason = "invalid" | "refused" | "form" | "none";
export const CONTACT_REASONS: Record<ContactReason, string> = {
  invalid: "e-mail invalide (revenu ou sans serveur de messagerie)",
  refused: "e-mail refusé (incertain ou messagerie gratuite, voir Réglages)",
  form: "seulement un formulaire ou une page de contact",
  none: "ni e-mail ni téléphone",
};
export const UNREACHABLE_MAX_TRIES = 2;
const AUTO_KINDS = ["GENERIC_BUSINESS", "PERSONAL_BUSINESS"];
const CLOSED_BODACC = ["radiation", "collective", "closure"];

export type FicheFields = {
  pipeline_stage?: string | null; activity_label?: string | null; website_status?: string | null; website_absence_confidence?: any; site_review?: string | null;
  provenance?: any; bodacc?: any; email?: string | null; email_kind?: string | null; phone?: string | null; auto_state?: string | null;
  do_not_contact?: any; status?: string | null; excluded_reason?: string | null; is_chain?: any; category?: string | null;
  contact_form?: any; contact_page?: string | null;
};

const obj = (v: any): Record<string, any> => {
  if (typeof v === "string") { try { v = JSON.parse(v); } catch { return {}; } }
  return v && typeof v === "object" && !Array.isArray(v) ? v : {};
};

export const ficheActive = (p: FicheFields) => !Number(p.do_not_contact ?? 0) && p.status !== "DO_NOT_CONTACT" && !p.excluded_reason && !Number(p.is_chain ?? 0)
  && (p.category ?? "") !== "IGNORER" && !CLOSED_BODACC.includes(String(obj(p.bodacc).kind ?? ""));

export const activityKnown = (p: FicheFields) => !["", "non classée"].includes(String(p.activity_label ?? "").trim().toLowerCase());

/** Site injoignable revu UNREACHABLE_MAX_TRIES fois et toujours injoignable : « site en panne », c'est tranché. */
export function unreachableSettled(p: FicheFields): boolean {
  const r = obj(obj(p.provenance).unreachable_retry);
  return Number(r.tries ?? 0) >= UNREACHABLE_MAX_TRIES && r.status === "UNREACHABLE";
}

/** Pourquoi le site est encore à trancher, ou null s'il est tranché (miroir de site_reason, complete.py). */
export function siteReason(p: FicheFields): SiteReason | null {
  if (p.website_status === "CONFIRMED" || p.website_status === "PROBABLE") return p.site_review === "PENDING" ? "review" : null;
  const prov = obj(p.provenance), recheck = obj(prov.site_recheck), pre = obj(prov.presend_site), places = obj(prov.places);
  if (p.website_status === "UNCERTAIN") return recheck.status == null ? "uncertain" : "uncertain_user";
  if (p.website_status === "UNREACHABLE") return unreachableSettled(p) ? null : "unreachable";
  if (p.website_status !== "NOT_FOUND" || Number(p.website_absence_confidence ?? 0) < ABSENCE_MIN) return "search";
  if (recheck.status == null) return "recheck";
  if (pre.doubt) return "presend";
  if (places.had_website === true && places.site_checked_at == null) return "places";
  return null;
}

/** « ok » (site tranché), « places » (tranché sauf le site de la fiche Google, vérifié juste avant l'envoi) ou « todo ». */
export function siteState(p: FicheFields): "ok" | "places" | "todo" {
  const why = siteReason(p);
  return why === null ? "ok" : why === "places" ? "places" : "todo";
}

export const emailInvalid = (p: FicheFields) => !!String(p.email ?? "").trim() && p.auto_state === "bounced";

export function emailOk(p: FicheFields, personal: boolean): boolean {
  const email = String(p.email ?? "").trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(email) || emailInvalid(p)) return false;
  if (personal) return [...AUTO_KINDS, "UNCERTAIN"].includes(String(p.email_kind));
  return AUTO_KINDS.includes(String(p.email_kind)) && !FREEMAIL.has(email.split("@")[1]);
}

export const phoneOk = (p: FicheFields) => !!String(p.phone ?? "").trim();

/** Pourquoi il n'y a pas de contact fiable, ou null (miroir de contact_reason, complete.py). */
export function contactReason(p: FicheFields, personal: boolean): ContactReason | null {
  if (emailOk(p, personal) || phoneOk(p)) return null;
  if (emailInvalid(p)) return "invalid";
  if (String(p.email ?? "").trim()) return "refused";
  if (Number(p.contact_form ?? 0) || String(p.contact_page ?? "").trim()) return "form";
  return "none";
}

/** Ce qui manque (vide = fiche complète), dans l'ordre de MISSING_LABELS. */
export function ficheMissing(p: FicheFields, personal: boolean): MissingCode[] {
  const out: MissingCode[] = [];
  if (!ficheActive(p)) out.push("entreprise");
  if (p.pipeline_stage !== "DONE") out.push("traitement");
  if (!activityKnown(p)) out.push("metier");
  if (siteState(p) !== "ok") out.push("site");
  if (!emailOk(p, personal) && !phoneOk(p)) out.push(emailInvalid(p) ? "email_invalide" : "contact");
  return out;
}

/** 'email' (complète, envoi automatique possible), 'phone' (complète, à contacter soi-même) ou null (incomplète). */
export function ficheChannel(p: FicheFields, personal: boolean): "email" | "phone" | null {
  if (ficheMissing(p, personal).length) return null;
  return emailOk(p, personal) ? "email" : "phone";
}

/** Pourquoi l'envoi automatique est refusé par ce critère (null = fiche complète avec e-mail). */
export function ficheReason(p: FicheFields, personal: boolean): string | null {
  const codes = ficheMissing(p, personal);
  if (codes.length) return `fiche incomplète : ${codes.map((c) => MISSING_LABELS[c]).join(", ")}`;
  return emailOk(p, personal) ? null : "pas d'e-mail fiable : à contacter par téléphone";
}

/** Le même critère en SQL (alias `p` = local_prospects), pour compter côté base (Statistiques → Qualité de la liste). Chaque morceau vaut 0 ou 1. */
export function completeSql(personal: boolean) {
  const b = (x: string) => `COALESCE((${x}), 0)`;
  const free = [...FREEMAIL].map((d) => `'${d}'`).join(",");
  const prov = (path: string) => `JSON_EXTRACT(p.provenance, '$.${path}')`;
  const active = b(`p.do_not_contact = 0 AND p.status <> 'DO_NOT_CONTACT' AND p.excluded_reason IS NULL AND p.is_chain = 0 AND COALESCE(p.category, '') <> 'IGNORER'
    AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(p.bodacc, '$.kind')), '') NOT IN ('radiation','collective','closure')`);
  const done = b(`p.pipeline_stage = 'DONE'`);
  const metier = b(`LOWER(TRIM(COALESCE(p.activity_label, ''))) NOT IN ('', 'non classée')`);
  const isNull = (path: string) => `(${prov(path)} IS NULL OR JSON_TYPE(${prov(path)}) = 'NULL')`;
  const settled = `(COALESCE(CAST(JSON_UNQUOTE(${prov("unreachable_retry.tries")}) AS UNSIGNED), 0) >= ${UNREACHABLE_MAX_TRIES}
    AND COALESCE(JSON_UNQUOTE(${prov("unreachable_retry.status")}), '') = 'UNREACHABLE')`;
  // raison pour laquelle le site reste à trancher (siteReason), NULL = tranché
  const siteReason = `CASE
    WHEN p.website_status IN ('CONFIRMED','PROBABLE') THEN IF(COALESCE(p.site_review, '') = 'PENDING', 'review', NULL)
    WHEN p.website_status = 'UNCERTAIN' THEN IF(${isNull("site_recheck.status")}, 'uncertain', 'uncertain_user')
    WHEN p.website_status = 'UNREACHABLE' THEN IF(${settled}, NULL, 'unreachable')
    WHEN COALESCE(p.website_status, '') <> 'NOT_FOUND' OR COALESCE(p.website_absence_confidence, 0) < ${ABSENCE_MIN} THEN 'search'
    WHEN ${isNull("site_recheck.status")} THEN 'recheck'
    WHEN COALESCE(JSON_UNQUOTE(${prov("presend_site.doubt")}), '') = 'true' THEN 'presend'
    WHEN COALESCE(JSON_UNQUOTE(${prov("places.had_website")}), '') = 'true' AND ${isNull("places.site_checked_at")} THEN 'places'
    ELSE NULL END`;
  const site = b(`(${siteReason}) IS NULL`);
  const invalid = b(`TRIM(COALESCE(p.email, '')) <> '' AND p.auto_state = 'bounced'`);
  const kinds = personal ? `p.email_kind IN ('GENERIC_BUSINESS','PERSONAL_BUSINESS','UNCERTAIN')`
    : `p.email_kind IN ('GENERIC_BUSINESS','PERSONAL_BUSINESS') AND SUBSTRING_INDEX(LOWER(TRIM(p.email)), '@', -1) NOT IN (${free})`;
  const email = b(`LOWER(TRIM(p.email)) REGEXP '^[^@[:space:]]+@[^@[:space:]]+[.][a-z]{2,}$' AND COALESCE(p.auto_state, '') <> 'bounced' AND ${kinds}`);
  const phone = b(`TRIM(COALESCE(p.phone, '')) <> ''`);
  const complete = `(${active} AND ${done} AND ${metier} AND ${site} AND (${email} OR ${phone}))`;
  // raison de l'absence de contact fiable (contactReason), NULL = e-mail acceptable ou téléphone
  const contactReason = `CASE WHEN ${email} OR ${phone} THEN NULL WHEN ${invalid} THEN 'invalid'
    WHEN TRIM(COALESCE(p.email, '')) <> '' THEN 'refused'
    WHEN COALESCE(p.contact_form, 0) <> 0 OR TRIM(COALESCE(p.contact_page, '')) <> '' THEN 'form' ELSE 'none' END`;
  return { active, done, metier, site, invalid, email, phone, complete, siteReason, contactReason };
}

/** Bloc « Qualité de la liste » : fiches à prospecter (actives, indépendantes, jamais contactées), complètes avec e-mail, complètes téléphone seul,
 *  et ce qui manque aux autres (une fiche peut cumuler plusieurs manques), avec la raison de chaque « site à trancher » (`site_<code>`) et de chaque
 *  « sans contact fiable » (`contact_<code>`), et où en sont les fiches en traitement (campagne en cours, erreur abandonnée, importée, autre). */
export function qualitySql(personal: boolean): string {
  const s = completeSql(personal);
  const sum = (x: string, alias: string) => `COALESCE(SUM(${x}), 0) AS ${alias}`;
  const reasons = (Object.keys(SITE_REASONS) as SiteReason[]).map((c) => sum(`NOT ${s.complete} AND (${s.siteReason}) = '${c}'`, `site_${c}`))
    .concat((["refused", "form", "none"] as ContactReason[]).map((c) => sum(`NOT ${s.complete} AND (${s.contactReason}) = '${c}'`, `contact_${c}`)));
  const running = `EXISTS (SELECT 1 FROM local_prospect_campaigns pc JOIN local_campaigns c ON c.id = pc.campaign_id
      WHERE pc.prospect_id = p.id AND c.enabled = 1 AND c.status IN ('queued','running'))`;
  return `SELECT COUNT(*) AS total,
      COALESCE(SUM(${s.complete} AND ${s.email}), 0) AS complete_email,
      COALESCE(SUM(${s.complete} AND NOT ${s.email}), 0) AS complete_phone,
      COALESCE(SUM(NOT ${s.complete} AND NOT ${s.done}), 0) AS processing,
      COALESCE(SUM(NOT ${s.complete} AND NOT ${s.metier}), 0) AS no_metier,
      COALESCE(SUM(NOT ${s.complete} AND NOT ${s.site}), 0) AS site_todo,
      COALESCE(SUM(NOT ${s.complete} AND NOT ${s.email} AND NOT ${s.phone} AND NOT ${s.invalid}), 0) AS no_contact,
      COALESCE(SUM(NOT ${s.complete} AND ${s.invalid} AND NOT ${s.phone}), 0) AS email_invalid,
      ${reasons.join(",\n      ")},
      ${sum(`NOT ${s.done} AND ${running}`, "processing_campaign")},
      ${sum(`NOT ${s.done} AND NOT ${running} AND p.pipeline_stage = 'ERROR' AND p.next_retry_at IS NULL`, "processing_abandoned")},
      ${sum(`NOT ${s.done} AND NOT ${running} AND p.pipeline_stage = 'IMPORTED'`, "processing_imported")}
    FROM local_prospects p WHERE ${s.active} AND p.contacted_at IS NULL AND p.status NOT IN ('CONTACTED','REPLIED','INTERESTED','WON','LOST')`;
}
