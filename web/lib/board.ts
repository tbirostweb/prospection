// Filtres et tris de la page « Entreprises » — partagés entre l'affichage (LocalBoard) et l'export CSV (/api/local/export),
// pour que le fichier exporté corresponde EXACTEMENT à la liste affichée. Module pur (aucun accès base).
import { LocalRow, simpleStatus, siteInfo } from "./local";

const km = (p: LocalRow) => (p.distance_km == null ? 999 : Number(p.distance_km));
export const hasContact = (p: LocalRow) => !!(p.email || p.phone || p.contact_form || p.contact_page);

export const BOARD_VIEWS: { id: string; label: string; test: (p: LocalRow) => boolean }[] = [
  { id: "all", label: "Toutes", test: () => true },
  { id: "tocontact", label: "À contacter", test: (p) => simpleStatus(p) === "À contacter" },
  { id: "nosite", label: "Pas de site trouvé", test: (p) => siteInfo(p).kind === "no" },
  { id: "down", label: "Site en panne", test: (p) => siteInfo(p).kind === "down" },
  { id: "site", label: "A un site", test: (p) => siteInfo(p).kind === "yes" },
  { id: "review", label: "Sites à vérifier", test: (p) => p.site_review === "PENDING" },
  { id: "worked", label: "Déjà contactées", test: (p) => ["CONTACTED", "REPLIED", "INTERESTED", "WON", "LOST"].includes(p.status) },
];
export const BOARD_SORTS: [string, string, (a: LocalRow, b: LocalRow) => number][] = [
  ["score", "Les plus intéressantes d'abord", (a, b) => (b.prospect_score ?? -1) - (a.prospect_score ?? -1)],
  ["distance", "Les plus proches d'abord", (a, b) => km(a) - km(b)],
  ["name", "Par nom", (a, b) => (a.trade_name || a.company_name).localeCompare(b.trade_name || b.company_name, "fr")],
];

export interface BoardFilters { view: string; q: string; sort: string; activity: string; contactOnly: boolean; showIgnored: boolean }

/** Base de la liste : sans les entreprises écartées, sauf si on demande à les voir. */
export const boardBase = (rows: LocalRow[], showIgnored: boolean) =>
  rows.filter((p) => showIgnored || (p.category !== "IGNORER" && !p.do_not_contact && !p.excluded_reason && !p.is_chain));

export function filterBoard(rows: LocalRow[], f: BoardFilters): LocalRow[] {
  const v = BOARD_VIEWS.find((x) => x.id === f.view) ?? BOARD_VIEWS[0];
  const needle = f.q.trim().toLowerCase();
  const cmp = (BOARD_SORTS.find((s) => s[0] === f.sort) ?? BOARD_SORTS[0])[2];
  return boardBase(rows, f.showIgnored).filter(v.test)
    .filter((p) => !f.activity || p.activity_label === f.activity)
    .filter((p) => !f.contactOnly || hasContact(p))
    .filter((p) => !needle || `${p.company_name} ${p.trade_name ?? ""} ${p.city ?? ""} ${p.activity_label ?? ""}`.toLowerCase().includes(needle))
    .sort(cmp);
}
