import { test } from "node:test";
import assert from "node:assert/strict";
import { CSV_BOM, EXPORT_HEADERS, ExportRow, buildExport, csvCell, exportFilename, exportLine, exportable, siteExport, toCsv } from "../lib/export";
import { filterBoard } from "../lib/board";

// Données 100 % synthétiques.
const row = (o: Partial<ExportRow> = {}): ExportRow => ({
  id: 1, siret: "00000000000018", siren: null, company_name: "SOCIETE EXEMPLE", trade_name: "Atelier Exemple", activity_key: "plombiers", activity_label: "Plombiers",
  city: "Villexemple", postal_code: "00000", distance_km: 2.35, company_created_at: null, website_url: null, website_status: null, website_confidence: null,
  seo_score: 12, performance_score: null, technical_score: null, modernization_opportunity: null, lighthouse_performance: null, issues: null,
  prospect_score: 77, score_stage: null, category: "A_CONTACTER", status: "QUALIFIED", is_chain: 0, excluded_reason: null, do_not_contact: 0,
  phone: "0100000000", email: "contact@exemple.test", email_kind: null, contact_page: null, bodacc: null, discovered_at: "2026-01-15T10:00:00Z", contacted_at: null,
  address: "1 rue de l'Exemple", social_links: [], campaigns: "Campagne test", ...o,
} as ExportRow);

test("CSV : guillemets doublés, point-virgule et retours à la ligne encapsulés", () => {
  assert.equal(csvCell('Le "Bon" ; coin'), '"Le ""Bon"" ; coin"');
  assert.equal(csvCell("ligne 1\nligne 2"), '"ligne 1\nligne 2"');
  assert.equal(csvCell(null), '""');
  assert.equal(csvCell(undefined), '""');
});

test("CSV : injection de formules neutralisée (= + - @ tabulation retour chariot)", () => {
  for (const bad of ["=HYPERLINK(\"x\")", "+33 1", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd"]) {
    assert.equal(csvCell(bad), `"'${bad.replace(/"/g, '""')}"`, bad);
  }
  assert.equal(csvCell("Normal = ok"), '"Normal = ok"');
});

test("CSV : BOM UTF-8, séparateur « ; », CRLF, accents conservés", () => {
  const csv = toCsv(["Nom", "Activité"], [["Café Éloïse", "Bar"]]);
  assert.ok(csv.startsWith(CSV_BOM));
  assert.equal(csv, `${CSV_BOM}"Nom";"Activité"\r\n"Café Éloïse";"Bar"\r\n`);
});

test("Site web : Oui / Non trouvé / À vérifier — jamais « non » avant une vraie recherche", () => {
  assert.deepEqual(siteExport({ website_status: "CONFIRMED", website_url: "https://exemple.test/" }), { label: "Oui", url: "https://exemple.test/" });
  assert.equal(siteExport({ website_status: "PROBABLE", website_url: "https://exemple.test/" }).label, "Oui (probable)");
  assert.equal(siteExport({ website_status: "UNREACHABLE", website_url: "https://exemple.test/" }).label, "Oui (inaccessible)");
  assert.deepEqual(siteExport({ website_status: "NOT_FOUND", website_absence_confidence: 0.8 }), { label: "Non trouvé", url: "" });
  assert.equal(siteExport({ website_status: "NOT_FOUND", website_absence_confidence: 0.3 }).label, "À vérifier", "recherche insuffisante");
  assert.equal(siteExport({ website_status: "NOT_FOUND" }).label, "À vérifier");
  assert.deepEqual(siteExport({ website_status: "UNCERTAIN", website_url: "https://candidat.test/" }), { label: "À vérifier", url: "" }, "candidat non validé jamais exporté");
  assert.equal(siteExport({ website_status: null }).label, "À vérifier");
  assert.equal(siteExport({ website_status: "CONFIRMED", website_url: "javascript:alert(1)" }).url, "");
});

test("Confidentialité : opposition, « ne plus contacter » et exclusions jamais exportées", () => {
  assert.equal(exportable(row()), true);
  assert.equal(exportable(row({ do_not_contact: 1 })), false);
  assert.equal(exportable(row({ status: "DO_NOT_CONTACT" })), false);
  assert.equal(exportable(row({ excluded_reason: "doublon" })), false);
  assert.equal(exportable(row(), [{ siren: "000000000" }]), false, "SIREN déduit du SIRET");
  assert.equal(exportable(row(), [{ siret: "00000000000018" }]), false);
  assert.equal(exportable(row(), [{ email: "CONTACT@exemple.test" }]), false);
  assert.equal(exportable(row({ website_url: "https://www.exemple.test/a" }), [{ domain: "exemple.test" }]), false);
  assert.equal(exportable(row(), [{ siren: "999999999" }]), true);
  const out = buildExport([row({ id: 1 }), row({ id: 2, do_not_contact: 1, trade_name: "Opposee" })], []);
  assert.equal(out.count, 1);
  assert.ok(!out.csv.includes("Opposee"));
});

test("Export : colonnes, pas de données internes, filtre e-mail, plafond", () => {
  const line = exportLine(row({ website_status: "CONFIRMED", website_url: "https://exemple.test/",
    social_links: [{ network: "Facebook", url: "https://facebook.com/exemple", via: "site", confidence: 1 }] }));
  assert.equal(line.length, EXPORT_HEADERS.length);
  const get = (h: string) => line[EXPORT_HEADERS.indexOf(h)];
  assert.equal(get("Nom"), "Atelier Exemple");
  assert.equal(get("Raison sociale"), "SOCIETE EXEMPLE");
  assert.equal(get("Site web"), "Oui");
  assert.equal(get("Téléphone"), "01 00 00 00 00");
  assert.equal(get("Facebook"), "https://facebook.com/exemple");
  assert.equal(get("Distance (km)"), "2,4");
  assert.equal(get("SIREN"), "000000000");
  assert.equal(get("SIRET"), "00000000000018");
  assert.equal(EXPORT_HEADERS[EXPORT_HEADERS.length - 1], "SIRET", "SIRET ajouté en FIN de liste : l'ordre historique des colonnes est conservé");
  assert.equal(EXPORT_HEADERS.indexOf("SIREN"), 19);
  assert.equal(exportLine(row({ siret: null, siren: "000000000" }))[EXPORT_HEADERS.indexOf("SIRET")], "", "pas de SIRET : cellule vide");
  assert.equal(get("Date d'ajout"), "2026-01-15");
  assert.equal(get("Statut de suivi"), "À contacter");
  assert.ok(!line.includes("77") && !EXPORT_HEADERS.some((h) => /score/i.test(h)), "aucun score exporté");

  const rows = [row({ id: 1 }), row({ id: 2, email: null }), row({ id: 3 })];
  assert.equal(buildExport(rows, [], { emailOnly: true }).count, 2);
  const capped = buildExport(rows, [], { max: 2 });
  assert.deepEqual([capped.count, capped.truncated], [2, true]);
  assert.equal(buildExport(rows, []).truncated, false);
});

test("Export : nom de fichier daté et filtres identiques à la page Entreprises", () => {
  assert.equal(exportFilename(new Date("2026-03-04T12:00:00Z")), "prospects-2026-03-04.csv");
  const rows = [row({ id: 1, trade_name: "Alpha" }), row({ id: 2, trade_name: "Beta", is_chain: 1 }), row({ id: 3, trade_name: "Gamma", email: null, phone: null })];
  const f = { view: "all", q: "", sort: "name", activity: "", contactOnly: false, showIgnored: false };
  assert.deepEqual(filterBoard(rows, f).map((r) => r.id), [1, 3]);
  assert.deepEqual(filterBoard(rows, { ...f, showIgnored: true }).map((r) => r.id), [1, 2, 3]);
  assert.deepEqual(filterBoard(rows, { ...f, contactOnly: true }).map((r) => r.id), [1]);
  assert.deepEqual(filterBoard(rows, { ...f, q: "gam" }).map((r) => r.id), [3]);
});
