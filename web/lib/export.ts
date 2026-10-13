// Export CSV des listes « À contacter » et « Entreprises ». Module pur (testé dans tests/export.test.ts).
// - UTF-8 avec BOM, séparateur « ; », fins de ligne CRLF : s'ouvre directement dans Excel (France).
// - Chaque cellule est entre guillemets (guillemets internes doublés) : « ; », retours à la ligne et guillemets sans risque.
// - Anti-injection de formules : une cellule commençant par = + - @ tabulation ou retour chariot est préfixée d'une apostrophe.
// - Confidentialité : une entreprise en opposition (« ne plus contacter », liste d'opposition) ou exclue n'est JAMAIS exportée.
// - Aucune donnée interne (score, preuves, détails techniques) ni personnelle superflue (dirigeant, notes).
import { LocalRow, formatPhone, simpleStatus, verifiedSocials } from "./local";
import { safeHref } from "./security";

export const EXPORT_MAX_ROWS = 5000;
export const CSV_BOM = "﻿";
const SEP = ";";
const EOL = "\r\n";

/** Une cellule CSV : neutralise les formules, échappe les guillemets, toujours entre guillemets. */
export function csvCell(value: unknown): string {
  let s = value == null ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return `"${s.replace(/"/g, '""')}"`;
}

export function toCsv(headers: string[], rows: unknown[][]): string {
  return CSV_BOM + [headers, ...rows].map((r) => r.map(csvCell).join(SEP)).join(EOL) + EOL;
}

/**
 * « A-t-il un site ? » pour l'export. Jamais « pas de site » avant une VRAIE recherche (miroir de siteInfo / siteSearched) :
 * « Non trouvé » seulement si la recherche a été faite avec une confiance d'absence suffisante, sinon « À vérifier ».
 * Un site PROBABLE n'est pas présenté comme confirmé.
 */
export function siteExport(p: { website_status?: string | null; website_absence_confidence?: any; website_url?: string | null }): { label: string; url: string } {
  const url = safeHref(p.website_url) ?? "";
  switch (p.website_status) {
    case "CONFIRMED": return { label: "Oui", url };
    case "PROBABLE": return { label: "Oui (probable)", url };
    case "UNREACHABLE": return { label: "Oui (inaccessible)", url };
    case "NOT_FOUND": return Number(p.website_absence_confidence ?? 0) >= 0.6 ? { label: "Non trouvé", url: "" } : { label: "À vérifier", url: "" };
    default: return { label: "À vérifier", url: "" }; // UNCERTAIN (site candidat non validé : URL jamais exportée) ou pas encore recherché
  }
}

export interface OptOut { siret?: string | null; siren?: string | null; email?: string | null; domain?: string | null }

const host = (u: string | null | undefined) => {
  const h = safeHref(u);
  return h ? new URL(h).hostname.toLowerCase().replace(/^www\./, "") : null;
};
const sirenOf = (p: { siren?: string | null; siret?: string | null }) => p.siren || (p.siret ? p.siret.slice(0, 9) : null);

/** Opposition, « ne plus contacter » ou exclusion : jamais exportée, quelle que soit la liste affichée. */
export function exportable(p: ExportRow, optOuts: OptOut[] = []): boolean {
  if (Number(p.do_not_contact) || p.status === "DO_NOT_CONTACT" || p.excluded_reason) return false;
  const siren = sirenOf(p);
  const email = p.email?.trim().toLowerCase() || null;
  const domain = host(p.website_url);
  return !optOuts.some((o) =>
    (o.siret && p.siret && o.siret === p.siret)
    || (o.siren && siren && o.siren === siren)
    || (o.email && email && o.email.trim().toLowerCase() === email)
    || (o.domain && domain && o.domain.toLowerCase().replace(/^www\./, "") === domain));
}

export type ExportRow = LocalRow & { siren?: string | null; campaigns?: string | null };

const day = (v: unknown) => {
  if (!v) return "";
  const d = v instanceof Date ? v : new Date(String(v));
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("sv-SE", { timeZone: "Europe/Paris" }); // AAAA-MM-JJ
};
const social = (p: LocalRow, network: string) => {
  const s = verifiedSocials(p).find((x) => x.network === network);
  return s ? safeHref(s.url) ?? "" : "";
};

export const EXPORT_HEADERS = [
  "Nom", "Raison sociale", "Activité", "Adresse", "Code postal", "Ville", "Distance (km)", "Site web", "URL du site", "E-mail", "Téléphone",
  "Formulaire de contact", "Facebook", "Instagram", "LinkedIn", "Statut de suivi", "Contacté le", "Campagne", "Date d'ajout", "SIREN",
];

export function exportLine(p: ExportRow): string[] {
  const site = siteExport(p);
  const name = (p.trade_name || p.company_name || "").replace(/\s+/g, " ").trim();
  const legal = (p.company_name || "").replace(/\s+/g, " ").trim();
  return [
    name, legal !== name ? legal : "", p.activity_label ?? "", p.address ?? "", p.postal_code ?? "", p.city ?? "",
    p.distance_km == null ? "" : Number(p.distance_km).toFixed(1).replace(".", ","),
    site.label, site.url, p.email ?? "", p.phone ? formatPhone(p.phone) : "",
    safeHref(p.contact_page) ?? "", social(p, "Facebook"), social(p, "Instagram"), social(p, "LinkedIn"),
    simpleStatus(p), day(p.contacted_at), p.campaigns ?? "", day(p.discovered_at), sirenOf(p) ?? "",
  ];
}

/** Lignes exportées : opposition / exclusion retirées, filtre « avec e-mail » facultatif, plafond EXPORT_MAX_ROWS. */
export function buildExport(rows: ExportRow[], optOuts: OptOut[], opts: { emailOnly?: boolean; max?: number } = {}) {
  const max = opts.max ?? EXPORT_MAX_ROWS;
  const kept = rows.filter((p) => exportable(p, optOuts)).filter((p) => !opts.emailOnly || !!p.email?.trim());
  return { csv: toCsv(EXPORT_HEADERS, kept.slice(0, max).map(exportLine)), count: Math.min(kept.length, max), truncated: kept.length > max };
}

export const exportFilename = (now = new Date()) => `prospects-${now.toLocaleDateString("sv-SE", { timeZone: "Europe/Paris" })}.csv`;
