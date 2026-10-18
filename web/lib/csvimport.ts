// Import CSV de prospects (symétrique de lib/export.ts). Module PUR : aucune requête, testé dans tests/csvimport.test.ts.
// Deux temps : APERÇU (analyse, aucune écriture) puis CONFIRMATION (lib/csvimport-db.ts, dans une transaction).
//
// Format attendu : l'export natif (UTF-8 avec ou sans BOM, « ; » — « , » détecté —, CRLF ou LF, cellules entre guillemets).
// Règles :
//  - rapprochement par SIRET si la colonne existe et est remplie, sinon par SIREN. Jamais par e-mail ni par adresse : un e-mail
//    partagé ne donne qu'un avertissement, deux entreprises ne sont jamais fusionnées ;
//  - « Statut de suivi » est un libellé CALCULÉ (simpleStatus) : il n'écrit JAMAIS de statut ni de catégorie ;
//  - une fiche existante n'est que COMPLÉTÉE (champs vides) : statut, notes, brouillons, dates, relances, réponse, opposition intacts ;
//    « Contacté le » ne remplit contacted_at que s'il est vide, sans toucher au statut ;
//  - opposition (local_do_not_contact : SIRET, SIREN, e-mail, domaine — ou fiche « ne plus contacter ») : ligne refusée ;
//  - « Non trouvé » n'est jamais importé comme « aucun site » (règle : pas d'absence sans vraie recherche) ; une URL importée est
//    un site « à vérifier » (UNCERTAIN), jamais un site confirmé sur parole ;
//  - anti-injection : l'apostrophe ajoutée par l'export devant = + - @ tabulation retour chariot est retirée ; rien n'est interprété.
import { createHash } from "crypto";
import { IMPORT_MAX_BYTES, IMPORT_MAX_ROWS, IMPORT_MIME_TYPES } from "./csvimport-limits";
import type { OptOut } from "./export";
import { LOCAL_ACTIVITIES, asList } from "./local";
import { safeHref } from "./security";

export { IMPORT_MAX_BYTES, IMPORT_MAX_ROWS };

export type Field = "name" | "legal" | "activity" | "address" | "postal" | "city" | "distance" | "siteLabel" | "url" | "email" | "phone"
  | "contactPage" | "facebook" | "instagram" | "linkedin" | "statusLabel" | "contactedOn" | "campaign" | "addedOn" | "siren" | "siret";

/** En-têtes reconnus (comparés sans accents ni casse). Ceux de l'export natif + quelques variantes courantes. */
const HEADER_FIELDS: Record<string, Field> = {
  "nom": "name", "raison sociale": "legal", "activite": "activity", "adresse": "address", "code postal": "postal", "ville": "city",
  "distance (km)": "distance", "distance": "distance", "site web": "siteLabel", "url du site": "url", "e-mail": "email", "email": "email", "courriel": "email",
  "telephone": "phone", "formulaire de contact": "contactPage", "facebook": "facebook", "instagram": "instagram", "linkedin": "linkedin",
  "statut de suivi": "statusLabel", "contacte le": "contactedOn", "campagne": "campaign", "date d'ajout": "addedOn", "siren": "siren", "siret": "siret",
};

export const fold = (s: string) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[’`]/g, "'").replace(/\s+/g, " ").trim().toLowerCase();
const sha256 = (s: string | Uint8Array) => createHash("sha256").update(s).digest("hex");
export const fileHash = (bytes: Uint8Array) => sha256(bytes);

// ─── Fichier : type, taille, encodage ──────────────────────────────────────────────────────────────────────────────

export function checkUpload(f: { filename?: unknown; mime?: unknown; size: number }): string | null {
  const name = typeof f.filename === "string" ? f.filename : "";
  if (!/\.csv$/i.test(name.trim())) return "Extension attendue : .csv";
  const mime = typeof f.mime === "string" ? f.mime.split(";")[0].trim().toLowerCase() : "";
  if (!IMPORT_MIME_TYPES.includes(mime)) return "Type de fichier refusé : un fichier CSV (texte) est attendu";
  if (f.size <= 0) return "Fichier vide";
  if (f.size > IMPORT_MAX_BYTES) return `Fichier trop volumineux (maximum ${IMPORT_MAX_BYTES / 1024 / 1024} Mo)`;
  return null;
}

/** Nom de fichier affichable et journalisable : sans chemin ni caractère de contrôle, borné. */
export const safeFilename = (raw: unknown) => String(raw ?? "").split(/[\\/]/).pop()!.replace(/[\u0000-\u001f\u007f<>]/g, "").trim().slice(0, 160) || "import.csv";

/** UTF-8 strict (BOM toléré et retiré). Un fichier Windows-1252 ou UTF-16 est refusé avec une consigne claire. */
export function decodeCsv(bytes: Uint8Array): { ok: true; text: string; bom: boolean } | { ok: false; error: string } {
  if (!bytes.length) return { ok: false, error: "Fichier vide" };
  if (bytes.length > IMPORT_MAX_BYTES) return { ok: false, error: `Fichier trop volumineux (maximum ${IMPORT_MAX_BYTES / 1024 / 1024} Mo)` };
  if ((bytes[0] === 0xff && bytes[1] === 0xfe) || (bytes[0] === 0xfe && bytes[1] === 0xff)) return { ok: false, error: "Encodage UTF-16 non pris en charge : enregistre le fichier en « CSV UTF-8 »" };
  const bom = bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf;
  let text: string;
  try { text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bom ? bytes.subarray(3) : bytes); }
  catch { return { ok: false, error: "Encodage non UTF-8 (Windows-1252 ?) : enregistre le fichier en « CSV UTF-8 »" }; }
  if (text.includes("\u0000")) return { ok: false, error: "Fichier binaire : un fichier CSV texte est attendu" };
  return { ok: true, text, bom };
}

// ─── Lecture CSV (RFC 4180, séparateur « ; » ou « , ») ────────────────────────────────────────────────────────────

/** Séparateur de la première ligne (hors guillemets) : « ; » par défaut, « , » s'il est plus fréquent. */
export function detectSeparator(text: string): ";" | "," {
  let semi = 0, comma = 0, quoted = false;
  for (const ch of text) {
    if (ch === '"') quoted = !quoted;
    else if (!quoted && (ch === "\n" || ch === "\r")) break;
    else if (!quoted && ch === ";") semi++;
    else if (!quoted && ch === ",") comma++;
  }
  return comma > semi ? "," : ";";
}

/** Découpe en enregistrements (guillemets doublés, retours à la ligne dans une cellule, fins CRLF / LF / CR). Borné à `maxRecords`. */
export function parseCsv(text: string, sep: string, maxRecords = IMPORT_MAX_ROWS + 2): { ok: true; records: string[][]; truncated: boolean } | { ok: false; error: string } {
  const records: string[][] = [];
  let row: string[] = [], cell = "", quoted = false, i = 0, line = 1, startLine = 1;
  const pushRow = () => { row.push(cell); records.push(row); row = []; cell = ""; };
  while (i < text.length) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') { cell += '"'; i += 2; continue; }
        quoted = false; i++; continue;
      }
      if (ch === "\n") line++;
      cell += ch; i++; continue;
    }
    if (ch === '"' && cell === "") { quoted = true; startLine = line; i++; continue; }
    if (ch === sep) { row.push(cell); cell = ""; i++; continue; }
    if (ch === "\r" || ch === "\n") {
      pushRow();
      if (ch === "\r" && text[i + 1] === "\n") i++;
      i++; line++;
      if (records.length > maxRecords) return { ok: true, records, truncated: true };
      continue;
    }
    cell += ch; i++;
  }
  if (quoted) return { ok: false, error: `Guillemet non fermé (à partir de la ligne ${startLine} du fichier)` };
  if (cell !== "" || row.length) pushRow();
  return { ok: true, records, truncated: false };
}

/** Retire l'apostrophe anti-formule de l'export (seulement devant = + - @ tabulation retour chariot) ; le reste est du texte brut. */
export function unprotect(raw: string): string {
  return /^'[=+\-@\t\r]/.test(raw) ? raw.slice(1) : raw;
}
const clean = (raw: string | undefined, max = 255) => unprotect(raw ?? "").replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim().slice(0, max);

export interface HeaderMap { cols: (Field | null)[]; errors: string[]; warnings: string[]; hasSiret: boolean }

export function mapHeader(cells: string[]): HeaderMap {
  const cols: (Field | null)[] = [];
  const errors: string[] = [], warnings: string[] = [];
  const seen = new Set<Field>();
  for (const c of cells) {
    const label = clean(c, 80);
    const f = HEADER_FIELDS[fold(label)] ?? null;
    if (f && seen.has(f)) errors.push(`Colonne en double : « ${label} »`);
    if (f) seen.add(f);
    else if (label) warnings.push(`Colonne ignorée : « ${label} »`);
    cols.push(f);
  }
  if (!seen.has("name")) errors.push("Colonne obligatoire manquante : « Nom »");
  if (!seen.has("siren") && !seen.has("siret")) errors.push("Colonne obligatoire manquante : « SIREN » (ou « SIRET »)");
  return { cols, errors, warnings, hasSiret: seen.has("siret") };
}

// ─── Normalisation d'une ligne ────────────────────────────────────────────────────────────────────────────────────

export interface ImportSocial { network: "Facebook" | "Instagram" | "LinkedIn"; url: string }
export interface ImportData {
  name: string; legal: string; activity: string; address: string; postal: string; city: string; distance: number | null;
  siteNotFound: boolean; url: string | null; email: string | null; phone: string | null; contactPage: string | null; socials: ImportSocial[];
  statusLabel: string; contactedOn: string | null; contactedAt: string | null; campaign: string; siren: string | null; siret: string | null;
}
export interface ImportRow { line: number; data: ImportData; hash: string; errors: string[]; warnings: string[]; optOutLabel: boolean }

const SOCIAL_HOSTS: Record<ImportSocial["network"], RegExp> = {
  Facebook: /(^|\.)(facebook\.com|fb\.com)$/, Instagram: /(^|\.)instagram\.com$/, LinkedIn: /(^|\.)linkedin\.com$/,
};
export const hostOf = (u: string | null | undefined) => {
  const h = safeHref(u);
  return h ? new URL(h).hostname.toLowerCase().replace(/^www\./, "") : null;
};

export function normalizePhone(raw: string): string | null {
  let s = raw.replace(/[\s.\-()\u00a0]/g, "");
  if (s.startsWith("+33")) s = "0" + s.slice(3);
  else if (s.startsWith("0033")) s = "0" + s.slice(4);
  if (s.startsWith("0(0)")) s = "0" + s.slice(4);
  return /^0[1-9]\d{8}$/.test(s) ? s : null;
}

export function normalizeEmail(raw: string): string | null {
  const s = raw.replace(/^mailto:/i, "").trim().toLowerCase();
  return s.length <= 255 && /^[^\s@<>()",;:'\\]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.[a-z]{2,}$/.test(s) ? s : null;
}

export function normalizeUrl(raw: string): string | null {
  const s = raw.trim();
  if (!s) return null;
  const href = safeHref(/^https?:\/\//i.test(s) ? s : /^[a-z0-9.-]+\.[a-z]{2,}(\/|$)/i.test(s) ? `https://${s}` : s);
  return href && href.length <= 512 && new URL(href).hostname.includes(".") ? href : null;
}

/** Jour civil à Paris (AAAA-MM-JJ) → instant UTC « AAAA-MM-JJ HH:MM:SS » à MIDI heure de Paris : réexporté, il redonne le même jour (cf. export.day). */
export function parisDayToUtc(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  const guess = new Date(Date.UTC(y, m - 1, d, 12));
  const parisHour = Number(new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Paris", hour: "2-digit", hourCycle: "h23" }).format(guess));
  const utc = new Date(Date.UTC(y, m - 1, d, 12 - (parisHour - 12)));
  return utc.toISOString().slice(0, 19).replace("T", " ");
}
export const parisToday = (now = new Date()) => now.toLocaleDateString("sv-SE", { timeZone: "Europe/Paris" });

/** « 2026-03-04 » ou « 04/03/2026 » (Excel) → « 2026-03-04 », si la date existe, n'est pas future et pas avant 2000. */
export function parseDay(raw: string, now = new Date()): string | null {
  let y: number, m: number, d: number;
  let r = /^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T].*)?$/.exec(raw);
  if (r) [y, m, d] = [Number(r[1]), Number(r[2]), Number(r[3])];
  else if ((r = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(raw))) [y, m, d] = [Number(r[3]), Number(r[2]), Number(r[1])];
  else return null;
  const dt = new Date(Date.UTC(y, m - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== m - 1 || dt.getUTCDate() !== d || y < 2000) return null;
  const iso = `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  return iso > parisToday(now) ? null : iso;
}

const FORMULA_LIKE = /^[=+\-@]/;

/** Transforme une ligne brute en données normalisées + erreurs (ligne refusée) et avertissements (valeur ignorée, ligne gardée). */
export function readRow(cells: string[], header: HeaderMap, line: number, now = new Date()): ImportRow {
  const errors: string[] = [], warnings: string[] = [];
  const raw: Partial<Record<Field, string>> = {};
  header.cols.forEach((f, i) => { if (f) raw[f] = cells[i] ?? ""; });
  if (cells.length !== header.cols.length) errors.push(`Nombre de colonnes incorrect (${cells.length} au lieu de ${header.cols.length})`);
  const get = (f: Field, max = 255) => clean(raw[f], max);

  const name = get("name");
  if (!name) errors.push("Nom manquant");
  for (const [f, label] of [["name", "Nom"], ["legal", "Raison sociale"], ["address", "Adresse"], ["city", "Ville"], ["activity", "Activité"]] as [Field, string][]) {
    if (FORMULA_LIKE.test(get(f))) warnings.push(`${label} commence par = + - ou @ : conservé comme simple texte`);
  }

  // Identité : SIRET (14 chiffres) et/ou SIREN (9 chiffres), cohérents entre eux.
  const idOf = (f: Field, len: number, label: string): string | null => {
    const v = get(f, 40).replace(/[\s.\u00a0]/g, "");
    if (!v) return null;
    if (/e\+?\d/i.test(v)) { errors.push(`${label} illisible (format scientifique d'Excel) : réexporte sans ouvrir le fichier dans Excel`); return null; }
    if (!new RegExp(`^\\d{${len}}$`).test(v)) { errors.push(`${label} invalide (${len} chiffres attendus)`); return null; }
    return v;
  };
  let siren = idOf("siren", 9, "SIREN");
  const siret = idOf("siret", 14, "SIRET");
  if (siret && siren && siret.slice(0, 9) !== siren) errors.push("SIRET incohérent avec le SIREN");
  if (siret && !siren) siren = siret.slice(0, 9);
  if (!siren && !siret && !errors.some((e) => /SIRE[NT]/.test(e))) errors.push("SIREN/SIRET absent : rapprochement impossible sans risque de doublon");

  const phoneRaw = get("phone", 40);
  const phone = phoneRaw ? normalizePhone(phoneRaw) : null;
  if (phoneRaw && !phone) warnings.push("Téléphone illisible : ignoré");
  const emailRaw = get("email");
  const email = emailRaw ? normalizeEmail(emailRaw) : null;
  if (emailRaw && !email) warnings.push("E-mail invalide : ignoré");
  const urlRaw = get("url", 600);
  const url = urlRaw ? normalizeUrl(urlRaw) : null;
  if (urlRaw && !url) warnings.push("URL du site invalide : ignorée");
  const formRaw = get("contactPage", 600);
  const contactPage = formRaw ? normalizeUrl(formRaw) : null;
  if (formRaw && !contactPage) warnings.push("URL du formulaire invalide : ignorée");
  const socials: ImportSocial[] = [];
  for (const [f, network] of [["facebook", "Facebook"], ["instagram", "Instagram"], ["linkedin", "LinkedIn"]] as [Field, ImportSocial["network"]][]) {
    const v = get(f, 600);
    if (!v) continue;
    const u = normalizeUrl(v);
    if (u && SOCIAL_HOSTS[network].test(new URL(u).hostname.toLowerCase())) socials.push({ network, url: u });
    else warnings.push(`Lien ${network} invalide : ignoré`);
  }
  let postal = get("postal", 20).replace(/\s/g, "");
  if (/^\d{4}$/.test(postal)) { postal = "0" + postal; warnings.push("Code postal à 4 chiffres (zéro initial perdu par Excel ?) : complété"); }
  else if (postal && !/^\d{5}$/.test(postal)) { warnings.push("Code postal invalide : ignoré"); postal = ""; }
  const distRaw = get("distance", 20).replace(",", ".");
  const dist = distRaw ? Number(distRaw) : NaN;
  const dayRaw = get("contactedOn", 40);
  const contactedOn = dayRaw ? parseDay(dayRaw, now) : null;
  if (dayRaw && !contactedOn) warnings.push("Date « Contacté le » invalide ou future : ignorée");
  const statusLabel = get("statusLabel", 60);

  const data: ImportData = {
    name, legal: get("legal"), activity: get("activity", 160), address: get("address"), postal, city: get("city", 128),
    distance: Number.isFinite(dist) && dist >= 0 && dist < 10000 ? Math.round(dist * 10) / 10 : null,
    siteNotFound: fold(get("siteLabel", 60)) === "non trouve", url, email, phone, contactPage, socials, statusLabel,
    contactedOn, contactedAt: contactedOn ? parisDayToUtc(contactedOn) : null, campaign: get("campaign", 160), siren, siret,
  };
  // Empreinte de la ligne : contenu importable seulement (libellé de statut, distance et date d'ajout, recalculés à chaque export, exclus).
  const hash = sha256(JSON.stringify([data.name, data.legal, data.activity, data.address, data.postal, data.city, data.url, data.email, data.phone,
    data.contactPage, data.socials, data.contactedOn, data.campaign, data.siren, data.siret]));
  return { line, data, hash, errors, warnings, optOutLabel: fold(statusLabel) === "ne plus contacter" };
}

export interface ParsedFile { ok: true; separator: ";" | ","; bom: boolean; header: HeaderMap; rows: ImportRow[]; fileHash: string }

/** Fichier complet → lignes normalisées. Les erreurs de FICHIER (encodage, en-têtes, trop de lignes) refusent tout l'import. */
export function readCsvFile(bytes: Uint8Array, now = new Date()): ParsedFile | { ok: false; error: string } {
  const dec = decodeCsv(bytes);
  if (!dec.ok) return dec;
  const separator = detectSeparator(dec.text);
  const parsed = parseCsv(dec.text, separator);
  if (!parsed.ok) return parsed;
  const records = parsed.records;
  if (!records.length || records[0].every((c) => !c.trim())) return { ok: false, error: "En-tête absent : la première ligne doit contenir les noms de colonnes" };
  const header = mapHeader(records[0]);
  if (header.errors.length) return { ok: false, error: header.errors.join(" · ") };
  const body = records.slice(1).map((cells, i) => ({ cells, line: i + 2 })).filter((r) => r.cells.some((c) => c.trim()));
  if (parsed.truncated || body.length > IMPORT_MAX_ROWS) return { ok: false, error: `Trop de lignes (maximum ${IMPORT_MAX_ROWS})` };
  if (!body.length) return { ok: false, error: "Aucune ligne de données" };
  return { ok: true, separator, bom: dec.bom, header, rows: body.map((r) => readRow(r.cells, header, r.line, now)), fileHash: fileHash(bytes) };
}

// ─── Rapprochement (aperçu) ───────────────────────────────────────────────────────────────────────────────────────

/** Ce que l'import lit d'une fiche existante (jamais modifié hors champs vides). */
export interface DbProspect {
  id: number; siren: string | null; siret: string | null; company_name: string; trade_name: string | null; activity_key?: string | null; activity_label: string | null;
  address: string | null; city: string | null; postal_code: string | null; phone: string | null; email: string | null; website_url: string | null;
  website_status: string | null; contact_page: string | null; social_links: any; contacted_at: any; do_not_contact: any; status: string;
}
export interface AnalyzeContext {
  prospects: DbProspect[];                    // fiches dont le SIREN ou le SIRET figure dans le fichier
  optOuts: OptOut[];                          // local_do_not_contact
  emailOwners: { id: number; email: string }[]; // fiches portant un e-mail du fichier (avertissement seulement)
  appliedHashes: Record<string, number>;      // empreinte de ligne déjà appliquée → fiche (encore existante)
}
export type Kind = "NEW" | "EXISTING" | "AMBIGUOUS" | "ERROR";
export interface Candidate { id: number; name: string; city: string | null; address: string | null; siret: string | null }
export interface AnalyzedRow {
  line: number; kind: Kind; reason: string; warnings: string[]; siren: string | null; siret: string | null; name: string; city: string;
  email: string | null; phone: string | null; matchId: number | null; candidates: Candidate[]; changes: string[]; alreadyImported: boolean;
}

const empty = (v: unknown) => v == null || String(v).trim() === "";
const pname = (p: DbProspect) => (p.trade_name || p.company_name || "").replace(/\s+/g, " ").trim();
const candidate = (p: DbProspect): Candidate => ({ id: p.id, name: pname(p), city: p.city, address: p.address, siret: p.siret });
const dnc = (p: DbProspect) => !!Number(p.do_not_contact) || p.status === "DO_NOT_CONTACT";

/** Opposition : SIRET, SIREN (déduit du SIRET), e-mail ou domaine présents dans local_do_not_contact. */
export function optedOut(d: Pick<ImportData, "siren" | "siret" | "email" | "url">, optOuts: OptOut[]): boolean {
  const domain = hostOf(d.url);
  const email = d.email?.toLowerCase() ?? null;
  return optOuts.some((o) =>
    (o.siret && d.siret && o.siret === d.siret)
    || (o.siren && d.siren && o.siren === d.siren)
    || (o.email && email && o.email.trim().toLowerCase() === email)
    || (o.domain && domain && o.domain.toLowerCase().replace(/^www\./, "") === domain));
}

const ACTIVITY_KEYS = new Map(LOCAL_ACTIVITIES.map(([k, label]) => [fold(label), k]));
export const activityKeyOf = (label: string) => ACTIVITY_KEYS.get(fold(label)) ?? null;

/**
 * Champs VIDES de la fiche que la ligne peut compléter. Jamais : statut, catégorie, notes, brouillons, relances, réponse, opposition,
 * nom légal, et aucune date autre que contacted_at (seulement si vide, statut inchangé).
 */
export function fillPlan(p: DbProspect, d: ImportData, siretTaken = false): { sets: Record<string, unknown>; fields: string[] } {
  const sets: Record<string, unknown> = {};
  const fields: string[] = [];
  const add = (col: string, v: unknown, label: string) => { sets[col] = v; fields.push(label); };
  if (empty(p.phone) && d.phone) add("phone", d.phone, "téléphone");
  if (empty(p.email) && d.email) add("email", d.email, "e-mail");
  if (empty(p.website_url) && empty(p.website_status) && d.url) { add("website_url", d.url, "site (à vérifier)"); sets.website_status = "UNCERTAIN"; }
  if (empty(p.contact_page) && d.contactPage) add("contact_page", d.contactPage, "formulaire");
  const socials = asList(p.social_links).filter((x: any) => x?.network && x?.url);
  const extra = d.socials.filter((s) => !socials.some((x: any) => x.network === s.network));
  if (extra.length) add("social_links", JSON.stringify([...socials, ...extra.map((s) => ({ ...s, via: "import", confidence: 0.5 }))]), extra.map((s) => s.network).join(", "));
  if (empty(p.address) && d.address) add("address", d.address, "adresse");
  if (empty(p.postal_code) && d.postal) add("postal_code", d.postal, "code postal");
  if (empty(p.city) && d.city) add("city", d.city, "ville");
  if (empty(p.activity_label) && d.activity) { add("activity_label", d.activity, "activité"); if (empty(p.activity_key) && activityKeyOf(d.activity)) sets.activity_key = activityKeyOf(d.activity); }
  const trade = d.legal && d.legal !== d.name ? d.name : "";
  if (empty(p.trade_name) && trade && trade !== p.company_name) add("trade_name", trade, "nom commercial");
  if (empty(p.siret) && d.siret && !siretTaken) add("siret", d.siret, "SIRET");
  if (empty(p.siren) && d.siren) add("siren", d.siren, "SIREN");
  if (empty(p.contacted_at) && d.contactedAt) add("contacted_at", d.contactedAt, "date de contact");
  return { sets, fields };
}

/** Rapproche chaque ligne : NOUVELLE / EXISTANTE / AMBIGUË / ERREUR, avec motif et avertissements. Aucune écriture. */
export function analyze(rows: ImportRow[], ctx: AnalyzeContext): AnalyzedRow[] {
  const bySiret = new Map<string, DbProspect>();
  const bySiren = new Map<string, DbProspect[]>();
  const byId = new Map<number, DbProspect>();
  for (const p of ctx.prospects) {
    byId.set(p.id, p);
    if (p.siret) bySiret.set(p.siret, p);
    const s = p.siren || (p.siret ? p.siret.slice(0, 9) : null);
    if (s) bySiren.set(s, [...(bySiren.get(s) ?? []), p]);
  }
  for (const list of bySiren.values()) list.sort((a, b) => a.id - b.id);
  // Doublons d'identité et e-mails partagés DANS le fichier.
  const identity = (r: ImportRow) => r.data.siret ? `t:${r.data.siret}` : r.data.siren ? `n:${r.data.siren}` : null;
  const firstLine = new Map<string, number>();
  const sirenLines = new Map<string, number[]>();
  const emailLines = new Map<string, Set<string>>();
  for (const r of rows) {
    if (r.errors.length) continue;
    const id = identity(r)!;
    if (!firstLine.has(id)) firstLine.set(id, r.line);
    sirenLines.set(r.data.siren!, [...(sirenLines.get(r.data.siren!) ?? []), r.line]);
    if (r.data.email) emailLines.set(r.data.email, new Set([...(emailLines.get(r.data.email) ?? []), id]));
  }
  const owners = new Map<string, number[]>();
  for (const o of ctx.emailOwners) {
    const e = o.email.trim().toLowerCase();
    owners.set(e, [...(owners.get(e) ?? []), o.id]);
  }

  return rows.map((r): AnalyzedRow => {
    const d = r.data;
    const out: AnalyzedRow = {
      line: r.line, kind: "ERROR", reason: "", warnings: [...r.warnings], siren: d.siren, siret: d.siret, name: d.name, city: d.city,
      email: d.email, phone: d.phone, matchId: null, candidates: [], changes: [], alreadyImported: false,
    };
    const fail = (reason: string) => ({ ...out, kind: "ERROR" as Kind, reason, matchId: null, candidates: [], changes: [] });
    if (r.errors.length) return fail(r.errors.join(" · "));
    if (r.optOutLabel) return fail("Opposition : le fichier indique « Ne plus contacter » (ligne jamais importée)");
    if (optedOut(d, ctx.optOuts)) return fail("Opposition : entreprise, e-mail ou domaine en liste « ne plus contacter »");
    const id = identity(r)!;
    if (firstLine.get(id) !== r.line) return fail(`Doublon dans le fichier : même ${d.siret ? "SIRET" : "SIREN"} qu'à la ligne ${firstLine.get(id)}`);

    // Avertissements : e-mail partagé (jamais une clé de rapprochement).
    if (d.email && (emailLines.get(d.email)?.size ?? 0) > 1) out.warnings.push("E-mail partagé avec une autre ligne du fichier (aucune fusion)");
    if (d.siteNotFound) out.warnings.push("« Non trouvé » importé comme site inconnu, à vérifier (aucune recherche faite par l'import)");
    if (d.statusLabel && d.contactedOn && fold(d.statusLabel) === "a contacter") out.warnings.push("Date de contact conservée, statut inchangé (le libellé du fichier n'est jamais appliqué)");

    const existing = (p: DbProspect, extra: Partial<AnalyzedRow> = {}): AnalyzedRow => {
      if (dnc(p)) return fail("Opposition : la fiche existante est en « ne plus contacter » (jamais mise à jour)");
      const siretTaken = !!d.siret && bySiret.has(d.siret) && bySiret.get(d.siret)!.id !== p.id;
      const plan = fillPlan(p, d, siretTaken);
      const shared = d.email ? (owners.get(d.email) ?? []).filter((x) => x !== p.id) : [];
      if (shared.length) out.warnings.push("E-mail déjà porté par une autre fiche (aucune fusion)");
      return { ...out, kind: "EXISTING", matchId: p.id, candidates: [candidate(p)], changes: plan.fields,
        reason: plan.fields.length ? `Fiche n° ${p.id} : champs vides complétés` : `Fiche n° ${p.id} : déjà à jour`, ...extra };
    };

    // Ligne déjà appliquée (même contenu) et fiche toujours là : rejouer le fichier ne change rien.
    const applied = ctx.appliedHashes[r.hash];
    if (applied && byId.has(applied)) {
      const p = byId.get(applied)!;
      if (dnc(p)) return fail("Opposition : la fiche existante est en « ne plus contacter » (jamais mise à jour)");
      return { ...out, kind: "EXISTING", matchId: p.id, candidates: [candidate(p)], changes: [], alreadyImported: true,
        reason: `Déjà importée (fiche n° ${p.id}) : aucun changement` };
    }

    const siblings = (bySiren.get(d.siren!) ?? []);
    const newRow = (reason: string): AnalyzedRow => {
      if (d.email && owners.has(d.email)) out.warnings.push("E-mail déjà porté par une autre fiche (aucune fusion)");
      const others = (sirenLines.get(d.siren!) ?? []).filter((l) => l !== r.line);
      if (!d.siret && others.length) {
        return { ...out, kind: "AMBIGUOUS", candidates: siblings.filter((p) => !dnc(p)).map(candidate),
          reason: `Même SIREN qu'à la ligne ${others[0]} du fichier, sans SIRET : nouvel établissement ou doublon ?` };
      }
      return { ...out, kind: "NEW", reason, changes: ["création"] };
    };

    if (d.siret) {
      const exact = bySiret.get(d.siret);
      if (exact) return existing(exact);
      const unknownEstab = siblings.filter((p) => !p.siret);
      if (unknownEstab.length) {
        if (unknownEstab.every(dnc)) return fail("Opposition : une fiche du même SIREN est en « ne plus contacter »");
        return { ...out, kind: "AMBIGUOUS", candidates: unknownEstab.filter((p) => !dnc(p)).map(candidate),
          reason: `SIRET inconnu, mais ${unknownEstab.length} fiche(s) du même SIREN sans SIRET : même établissement ?` };
      }
      if (siblings.length) out.warnings.push(`Autre(s) établissement(s) du même SIREN déjà connu(s) : ${siblings.length}`);
      return newRow("Nouvel établissement (SIRET inconnu)");
    }
    if (siblings.length === 1) return existing(siblings[0]);
    if (siblings.length > 1) {
      const open = siblings.filter((p) => !dnc(p));
      if (!open.length) return fail("Opposition : les fiches de ce SIREN sont en « ne plus contacter »");
      return { ...out, kind: "AMBIGUOUS", candidates: open.map(candidate),
        reason: `${siblings.length} établissements connus pour ce SIREN : choisis lequel (ou crée-en un nouveau)` };
    }
    return newRow("Nouvelle entreprise (SIREN inconnu)");
  });
}

export interface ImportCounts { total: number; new: number; existing: number; ambiguous: number; errors: number; warnings: number; changes: number }
export function countKinds(rows: AnalyzedRow[]): ImportCounts {
  return {
    total: rows.length, new: rows.filter((r) => r.kind === "NEW").length, existing: rows.filter((r) => r.kind === "EXISTING").length,
    ambiguous: rows.filter((r) => r.kind === "AMBIGUOUS").length, errors: rows.filter((r) => r.kind === "ERROR").length,
    warnings: rows.filter((r) => r.warnings.length).length, changes: rows.filter((r) => r.kind === "EXISTING" && r.changes.length).length,
  };
}

/** Jeton de l'aperçu : la confirmation est refusée si le fichier ou le résultat du rapprochement a changé entre-temps. */
export function previewToken(fileHashHex: string, rows: AnalyzedRow[]): string {
  return sha256(JSON.stringify([fileHashHex, rows.map((r) => [r.line, r.kind, r.matchId, r.candidates.map((c) => c.id), r.changes])]));
}

// ─── Décisions de l'utilisateur (confirmation) ────────────────────────────────────────────────────────────────────

export type Resolution = { action: "link"; id: number } | { action: "create" } | { action: "ignore" };
export type Decision =
  | { line: number; op: "create"; reason: string }
  | { line: number; op: "update"; id: number; reason: string }
  | { line: number; op: "none"; outcome: "UNCHANGED" | "IGNORED" | "SKIPPED" | "ERROR"; id: number | null; reason: string };

/** Résolutions reçues du navigateur (non fiables) : seules les formes connues sont gardées. */
export function parseResolutions(raw: unknown): Record<number, Resolution> {
  const out: Record<number, Resolution> = {};
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return out;
  for (const [k, v] of Object.entries(raw as Record<string, any>)) {
    const line = Number(k);
    if (!Number.isInteger(line) || line < 2 || line > IMPORT_MAX_ROWS * 10 || !v || typeof v !== "object") continue;
    if (v.action === "link" && Number.isInteger(v.id) && v.id > 0) out[line] = { action: "link", id: v.id };
    else if (v.action === "create" || v.action === "ignore") out[line] = { action: v.action };
  }
  return out;
}

/** Applique les choix : lignes décochées ignorées, ambiguïtés résolues (sinon erreur), aucune ligne en erreur appliquée. */
export function decide(rows: AnalyzedRow[], resolutions: Record<number, Resolution>, skip: Set<number>): { ok: true; decisions: Decision[] } | { ok: false; error: string } {
  const decisions: Decision[] = [];
  for (const r of rows) {
    if (r.kind === "ERROR") { decisions.push({ line: r.line, op: "none", outcome: "ERROR", id: null, reason: r.reason }); continue; }
    if (skip.has(r.line)) { decisions.push({ line: r.line, op: "none", outcome: "SKIPPED", id: r.matchId, reason: "Décochée dans l'aperçu" }); continue; }
    if (r.kind === "NEW") { decisions.push({ line: r.line, op: "create", reason: r.reason }); continue; }
    if (r.kind === "EXISTING") {
      decisions.push(r.changes.length ? { line: r.line, op: "update", id: r.matchId!, reason: r.reason }
        : { line: r.line, op: "none", outcome: "UNCHANGED", id: r.matchId, reason: r.reason });
      continue;
    }
    const res = resolutions[r.line];
    if (!res) return { ok: false, error: `Ligne ${r.line} : ambiguïté non résolue` };
    if (res.action === "ignore") decisions.push({ line: r.line, op: "none", outcome: "IGNORED", id: null, reason: "Ambiguïté : ignorée" });
    else if (res.action === "create") decisions.push({ line: r.line, op: "create", reason: "Ambiguïté : nouvel établissement créé" });
    else {
      if (!r.candidates.some((c) => c.id === res.id)) return { ok: false, error: `Ligne ${r.line} : fiche choisie hors des propositions` };
      decisions.push({ line: r.line, op: "update", id: res.id, reason: `Ambiguïté : rattachée à la fiche n° ${res.id}` });
    }
  }
  return { ok: true, decisions };
}

/** Empreinte d'une fiche créée par import (colonne NOT NULL UNIQUE) : propre à l'import, ne peut pas entrer en collision avec celle du worker. */
export function importFingerprint(d: ImportData): string {
  return sha256(`csv-import|${d.siret ?? ""}|${d.siren ?? ""}|${fold(d.name)}|${fold(d.address)}|${fold(d.city)}`);
}

/**
 * Colonnes d'une NOUVELLE fiche. Source « import CSV » (provenance + local_prospect_sources), étape `IMPORTED` : la fiche n'est rattachée
 * à AUCUNE campagne, le worker (qui ne traite que les fiches de ses campagnes) ne la recherche ni ne l'enrichit. Statut DISCOVERED par
 * défaut, jamais déduit du libellé du fichier. Une URL fournie devient un site « à vérifier » (UNCERTAIN), jamais confirmé ; « Non trouvé »
 * reste inconnu (website_status NULL).
 */
export function createValues(d: ImportData, importId: number, nowIso: string): Record<string, unknown> {
  const legal = d.legal || d.name;
  const provenance: Record<string, unknown> = { import_csv: { source: "import CSV", import_id: importId, at: nowIso, campaign: d.campaign || null } };
  if (d.url) provenance.website = { source: "import CSV", status: "UNCERTAIN", note: "site déclaré par le fichier, à vérifier" };
  if (d.phone) provenance.phone = { source: "import CSV" };
  if (d.email) provenance.email = { source: "import CSV" };
  return {
    siren: d.siren, siret: d.siret, fingerprint: importFingerprint(d), company_name: legal.slice(0, 255),
    trade_name: d.legal && d.legal !== d.name ? d.name : null, activity_key: d.activity ? activityKeyOf(d.activity) : null,
    activity_label: d.activity || null, address: d.address || null, city: d.city || null, postal_code: d.postal || null, distance_km: d.distance,
    website_url: d.url, website_status: d.url ? "UNCERTAIN" : null, phone: d.phone, email: d.email, contact_page: d.contactPage,
    social_links: d.socials.length ? JSON.stringify(d.socials.map((s) => ({ ...s, via: "import", confidence: 0.5 }))) : null,
    contacted_at: d.contactedAt, status: "DISCOVERED", pipeline_stage: "IMPORTED", provenance: JSON.stringify(provenance),
  };
}
