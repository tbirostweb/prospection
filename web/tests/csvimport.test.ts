import { test } from "node:test";
import assert from "node:assert/strict";
import { CSV_BOM, EXPORT_HEADERS, ExportRow, buildExport, toCsv } from "../lib/export";
import {
  AnalyzeContext, DbProspect, ImportRow, analyze, checkUpload, countKinds, createValues, decide, decodeCsv, detectSeparator, fillPlan, parisDayToUtc,
  parseCsv, parseDay, parseResolutions, previewToken, readCsvFile, unprotect,
} from "../lib/csvimport";
import { IMPORT_MAX_BYTES, IMPORT_MAX_ROWS } from "../lib/csvimport-limits";

// Données 100 % FICTIVES, générées : aucun nom, e-mail ni SIREN réel.
const enc = (s: string) => new TextEncoder().encode(s);
const NOW = new Date("2026-10-18T12:00:00Z");
const siren = (n: number) => String(100000000 + n);                     // 9 chiffres fictifs
const siret = (n: number, e = 1) => siren(n) + String(e).padStart(5, "0"); // 14 chiffres fictifs

const exportRow = (n: number, o: Partial<ExportRow> = {}): ExportRow => ({
  id: n, siret: siret(n), siren: siren(n), company_name: `SOCIETE FICTIVE ${n}`, trade_name: `Atelier Fictif ${n}`, activity_key: "plombiers", activity_label: "Plombiers",
  city: "Villefictive", postal_code: "00100", distance_km: 3.2, company_created_at: null, website_url: `https://fictif-${n}.test/`, website_status: "CONFIRMED",
  website_confidence: 0.95, seo_score: null, performance_score: null, technical_score: null, modernization_opportunity: null, lighthouse_performance: null, issues: null,
  prospect_score: 70, score_stage: "FINAL", category: "A_CONTACTER", status: "QUALIFIED", is_chain: 0, excluded_reason: null, do_not_contact: 0,
  phone: "0100000000", email: `contact@fictif-${n}.test`, email_kind: null, contact_page: null, bodacc: null, discovered_at: "2026-01-15T10:00:00Z", contacted_at: null,
  address: `${n} rue Imaginaire`, social_links: [{ network: "Facebook", url: `https://facebook.com/fictif${n}`, via: "site", confidence: 1 }], campaigns: "Campagne test", ...o,
} as ExportRow);

/** CSV natif de l'application (même code que l'export). */
const nativeCsv = (rows: ExportRow[]) => buildExport(rows, []).csv;

const dbRow = (n: number, o: Partial<DbProspect> = {}): DbProspect => ({
  id: n, siren: siren(n), siret: siret(n), company_name: `SOCIETE FICTIVE ${n}`, trade_name: null, activity_label: null, activity_key: null, address: null,
  city: null, postal_code: null, phone: null, email: null, website_url: null, website_status: null, contact_page: null, social_links: null, contacted_at: null,
  do_not_contact: 0, status: "DISCOVERED", ...o,
});
const ctx = (o: Partial<AnalyzeContext> = {}): AnalyzeContext => ({ prospects: [], optOuts: [], emailOwners: [], appliedHashes: {}, ...o });
function rowsOf(csv: string | Uint8Array): ImportRow[] {
  const f = readCsvFile(typeof csv === "string" ? enc(csv) : csv, NOW);
  if (!f.ok) throw new Error(f.error);
  return f.rows;
}
/** CSV minimal à la main (séparateur « ; », CRLF). */
const csvOf = (headers: string[], lines: string[][]) => toCsv(headers, lines);

test("Format natif : BOM, « ; », CRLF, guillemets — aller-retour export → import fidèle", () => {
  const csv = nativeCsv([exportRow(1, { contacted_at: "2026-03-04T09:30:00Z" }), exportRow(2, { trade_name: null, siret: null })]);
  assert.ok(csv.startsWith(CSV_BOM) && csv.includes("\r\n"));
  const f = readCsvFile(enc(csv), NOW);
  assert.ok(f.ok);
  if (!f.ok) return;
  assert.equal(f.separator, ";");
  assert.equal(f.bom, true);
  assert.equal(f.header.hasSiret, true);
  const [a, b] = f.rows;
  assert.deepEqual(a.errors, []);
  assert.equal(a.line, 2);
  assert.equal(a.data.name, "Atelier Fictif 1");
  assert.equal(a.data.legal, "SOCIETE FICTIVE 1");
  assert.equal(a.data.siren, siren(1));
  assert.equal(a.data.siret, siret(1));
  assert.equal(a.data.phone, "0100000000", "téléphone formaté « 01 00 00 00 00 » renormalisé");
  assert.equal(a.data.email, "contact@fictif-1.test");
  assert.equal(a.data.url, "https://fictif-1.test/");
  assert.deepEqual(a.data.socials, [{ network: "Facebook", url: "https://facebook.com/fictif1" }]);
  assert.equal(a.data.contactedOn, "2026-03-04");
  assert.equal(a.data.campaign, "Campagne test");
  assert.equal(a.data.postal, "00100");
  assert.equal(b.data.siret, null, "SIRET absent : cellule vide tolérée");
  assert.equal(b.data.siren, siren(2));
});

test("Anti-formule : l'apostrophe de l'export est retirée, rien n'est interprété", () => {
  assert.equal(unprotect("'=SUM(A1)"), "=SUM(A1)");
  assert.equal(unprotect("'+33 1 00 00 00 00"), "+33 1 00 00 00 00");
  assert.equal(unprotect("'-x"), "-x");
  assert.equal(unprotect("'@x"), "@x");
  assert.equal(unprotect("'\tx"), "\tx");
  assert.equal(unprotect("'Aube"), "'Aube", "apostrophe ordinaire conservée");
  const csv = nativeCsv([exportRow(1, { trade_name: "=HYPERLINK(\"http://x.test\")", phone: null, email: null, social_links: [] })]);
  assert.ok(csv.includes(`"'=HYPERLINK(""http://x.test"")"`), "l'export a bien protégé la cellule");
  const [r] = rowsOf(csv);
  assert.equal(r.data.name, "=HYPERLINK(\"http://x.test\")", "texte brut, sans apostrophe");
  assert.ok(r.warnings.some((w) => /simple texte/.test(w)));
  const [p] = rowsOf(csvOf(["Nom", "SIREN", "Téléphone"], [["Test", siren(3), "+33 1 00 00 00 01"]]));
  assert.equal(p.data.phone, "0100000001", "+33 protégé par l'export puis normalisé");
});

test("Séparateur « , » détecté, fins de ligne LF, retours à la ligne dans une cellule", () => {
  const csv = `Nom,SIREN,Adresse\n"Fictif, et fils",${siren(4)},"1 rue A\nbâtiment B"\n`;
  assert.equal(detectSeparator(csv), ",");
  const f = readCsvFile(enc(csv), NOW);
  assert.ok(f.ok);
  if (!f.ok) return;
  assert.equal(f.separator, ",");
  assert.equal(f.bom, false);
  assert.equal(f.rows[0].data.name, "Fictif, et fils");
  assert.equal(f.rows[0].data.address, "1 rue A bâtiment B");
  const quoted = parseCsv('"a;b";"c""d"\r\n', ";");
  assert.ok(quoted.ok && quoted.records[0][0] === "a;b" && quoted.records[0][1] === 'c"d');
  const open = parseCsv('"a;b\r\n', ";");
  assert.equal(open.ok, false);
});

test("Fichier refusé : colonnes manquantes, en double, encodage, taille, nombre de lignes, type", () => {
  const miss = readCsvFile(enc(csvOf(["Ville", "E-mail"], [["X", "a@b.test"]])), NOW);
  assert.ok(!miss.ok && /« Nom »/.test(miss.error) && /SIREN/.test(miss.error));
  const dup = readCsvFile(enc(csvOf(["Nom", "SIREN", "siren"], [["X", siren(1), siren(1)]])), NOW);
  assert.ok(!dup.ok && /double/.test(dup.error));
  const latin1 = new Uint8Array([...enc("Nom;SIREN\r\nCaf"), 0xe9, ...enc(`;${siren(1)}\r\n`)]);
  const bad = decodeCsv(latin1);
  assert.ok(!bad.ok && /UTF-8/.test(bad.error));
  assert.ok(!decodeCsv(new Uint8Array([0xff, 0xfe, 0x4e, 0x00])).ok, "UTF-16 refusé");
  assert.ok(!decodeCsv(new Uint8Array(IMPORT_MAX_BYTES + 1).fill(0x41)).ok, "trop gros");
  const many = csvOf(["Nom", "SIREN"], Array.from({ length: IMPORT_MAX_ROWS + 1 }, (_, i) => [`F${i}`, siren(i)]));
  const tooMany = readCsvFile(enc(many), NOW);
  assert.ok(!tooMany.ok && /Trop de lignes/.test(tooMany.error));
  const ok = readCsvFile(enc(csvOf(["Nom", "SIREN"], Array.from({ length: IMPORT_MAX_ROWS }, (_, i) => [`F${i}`, siren(i)]))), NOW);
  assert.ok(ok.ok, "5 000 lignes acceptées");
  assert.ok(!readCsvFile(enc(""), NOW).ok);
  assert.ok(!readCsvFile(enc(csvOf(["Nom", "SIREN"], [])), NOW).ok, "aucune ligne de données");
  assert.match(checkUpload({ filename: "liste.xlsx", mime: "text/csv", size: 10 })!, /Extension/);
  assert.match(checkUpload({ filename: "liste.csv", mime: "application/pdf", size: 10 })!, /Type/);
  assert.match(checkUpload({ filename: "liste.csv", mime: "text/csv", size: IMPORT_MAX_BYTES + 1 })!, /volumineux/);
  assert.equal(checkUpload({ filename: "liste.CSV", mime: "application/vnd.ms-excel", size: 10 }), null);
  assert.equal(checkUpload({ filename: "liste.csv", mime: "", size: 10 }), null);
});

test("Ligne refusée : SIREN/SIRET absent, invalide, format scientifique, incohérent, nombre de colonnes", () => {
  const rows = rowsOf(csvOf(["Nom", "SIREN", "SIRET"], [
    ["Sans id", "", ""], ["Court", "12345", ""], ["Excel", "1,00000001E+13", ""], ["Incohérent", siren(1), siret(2)], ["", siren(9), ""],
  ]));
  const errs = rows.map((r) => r.errors.join(" "));
  assert.match(errs[0], /absent/);
  assert.match(errs[1], /SIREN invalide/);
  assert.match(errs[2], /scientifique/);
  assert.match(errs[3], /incohérent/);
  assert.match(errs[4], /Nom manquant/);
  const cols = rowsOf("Nom;SIREN\r\nA;" + siren(1) + ";extra\r\n");
  assert.match(cols[0].errors.join(), /Nombre de colonnes/);
  const analyzed = analyze(rows, ctx());
  assert.ok(analyzed.every((r) => r.kind === "ERROR"));
});

test("Ancien fichier sans colonne SIRET : rapprochement par SIREN (1 fiche = existante, plusieurs = ambiguë, 0 = nouvelle)", () => {
  const headers = EXPORT_HEADERS.filter((h) => h !== "SIRET");
  const csv = toCsv(headers, [[...Array(19).fill(""), siren(1)], [...Array(19).fill(""), siren(2)], [...Array(19).fill(""), siren(3)]].map((l, i) => { l[0] = `Fictif ${i}`; return l; }));
  const f = readCsvFile(enc(csv), NOW);
  assert.ok(f.ok && !f.header.hasSiret);
  const res = analyze(rowsOf(csv), ctx({ prospects: [dbRow(1), dbRow(21, { siren: siren(2), siret: siret(2, 1) }), dbRow(22, { siren: siren(2), siret: siret(2, 2) })] }));
  assert.deepEqual(res.map((r) => r.kind), ["EXISTING", "AMBIGUOUS", "NEW"]);
  assert.equal(res[0].matchId, 1);
  assert.deepEqual(res[1].candidates.map((c) => c.id), [21, 22], "les deux établissements proposés");
  assert.equal(res[1].matchId, null, "jamais de choix automatique");
});

test("SIRET : correspondance exacte = existante ; SIRET inconnu mais fiche du même SIREN sans SIRET = ambiguë ; sinon nouvel établissement", () => {
  const rows = rowsOf(csvOf(["Nom", "SIREN", "SIRET"], [["A", siren(1), siret(1)], ["B", siren(2), siret(2, 9)], ["C", siren(3), siret(3, 9)]]));
  const res = analyze(rows, ctx({ prospects: [dbRow(1), dbRow(2, { siret: null }), dbRow(3, { siret: siret(3, 1) })] }));
  assert.deepEqual(res.map((r) => r.kind), ["EXISTING", "AMBIGUOUS", "NEW"]);
  assert.ok(res[2].warnings.some((w) => /même SIREN/.test(w)), "autre établissement signalé");
});

test("Préservation du suivi : seuls les champs VIDES sont complétés, jamais statut, notes, brouillon, dates, relances, opposition", () => {
  const [r] = rowsOf(nativeCsv([exportRow(1, { contacted_at: "2026-02-02T10:00:00Z" })]));
  const worked = dbRow(1, {
    status: "CONTACTED", phone: "0199999999", contacted_at: new Date("2026-01-10T08:00:00Z"), website_url: "https://autre.test/", website_status: "CONFIRMED",
    social_links: [{ network: "Facebook", url: "https://facebook.com/autre", via: "site", confidence: 1 }],
    // colonnes qui n'existent même pas dans le plan : notes, brouillon, relances, réponse
    ...({ notes: "note perso", draft_message: "brouillon", followups: 2, response_status: "REPLIED" } as any),
  });
  const plan = fillPlan(worked, r.data);
  const allowed = ["phone", "email", "website_url", "website_status", "contact_page", "social_links", "address", "postal_code", "city", "activity_label", "activity_key",
    "trade_name", "siret", "siren", "contacted_at"];
  assert.ok(Object.keys(plan.sets).every((k) => allowed.includes(k)), Object.keys(plan.sets).join());
  for (const k of ["status", "category", "notes", "draft_message", "draft_subject", "followups", "response_status", "do_not_contact", "last_contacted_at", "company_name"])
    assert.ok(!(k in plan.sets), k);
  assert.ok(!("phone" in plan.sets), "téléphone existant conservé");
  assert.ok(!("contacted_at" in plan.sets), "date de contact existante conservée");
  assert.ok(!("website_url" in plan.sets), "site existant conservé");
  assert.ok(!("social_links" in plan.sets), "réseau déjà connu conservé");
  assert.equal(plan.sets.email, "contact@fictif-1.test", "e-mail vide complété");
  const res = analyze([r], ctx({ prospects: [worked] }));
  assert.equal(res[0].kind, "EXISTING");
  assert.ok(res[0].changes.includes("e-mail"));
});

test("Ligne datée « À contacter » : contacted_at complété (jour Paris → UTC), statut jamais modifié", () => {
  const [r] = rowsOf(nativeCsv([exportRow(1, { contacted_at: "2026-07-14T08:00:00Z" })]));
  assert.equal(r.data.statusLabel, "À contacter");
  const plan = fillPlan(dbRow(1, { status: "QUALIFIED" }), r.data);
  assert.equal(plan.sets.contacted_at, "2026-07-14 10:00:00", "midi à Paris (UTC+2 en été)");
  assert.ok(!("status" in plan.sets));
  const res = analyze([r], ctx({ prospects: [dbRow(1, { status: "QUALIFIED" })] }));
  assert.ok(res[0].warnings.some((w) => /statut inchangé/.test(w)));
  const created = createValues(r.data, 1, "2026-10-18T12:00:00Z");
  assert.equal(created.status, "DISCOVERED", "une nouvelle fiche n'hérite jamais du libellé du fichier");
  assert.equal(created.contacted_at, "2026-07-14 10:00:00");
  assert.ok(!("category" in created));
});

test("Dates : jour de Paris → UTC cohérent avec l'export (hiver/été), formats acceptés, date future refusée", () => {
  assert.equal(parisDayToUtc("2026-01-15"), "2026-01-15 11:00:00");
  assert.equal(parisDayToUtc("2026-07-15"), "2026-07-15 10:00:00");
  for (const d of ["2026-03-29", "2026-10-25", "2026-12-31"]) {
    const back = new Date(parisDayToUtc(d).replace(" ", "T") + "Z").toLocaleDateString("sv-SE", { timeZone: "Europe/Paris" });
    assert.equal(back, d, `réexport du ${d}`);
  }
  assert.equal(parseDay("04/03/2026", NOW), "2026-03-04", "format Excel JJ/MM/AAAA");
  assert.equal(parseDay("2026-02-30", NOW), null);
  assert.equal(parseDay("2026-12-01", NOW), null, "future");
  assert.equal(parseDay("1999-01-01", NOW), null);
});

test("E-mail partagé : avertissement visible, jamais de fusion ni de clé de rapprochement", () => {
  const csv = nativeCsv([exportRow(1, { email: "commun@fictif.test" }), exportRow(2, { email: "commun@fictif.test" })]);
  const res = analyze(rowsOf(csv), ctx({ prospects: [], emailOwners: [{ id: 77, email: "COMMUN@fictif.test" }] }));
  assert.deepEqual(res.map((r) => r.kind), ["NEW", "NEW"], "deux entreprises distinctes, deux créations");
  for (const r of res) {
    assert.ok(r.warnings.some((w) => /autre ligne du fichier/.test(w)));
    assert.ok(r.warnings.some((w) => /autre fiche/.test(w)));
    assert.equal(r.matchId, null, "la fiche 77 qui porte l'e-mail n'est jamais choisie");
  }
});

test("Opposition : SIRET, SIREN, e-mail, domaine, fiche « ne plus contacter » ou libellé du fichier → ligne refusée", () => {
  const rows = rowsOf(nativeCsv([1, 2, 3, 4, 5, 6].map((n) => exportRow(n))));
  const res = analyze(rows, ctx({
    optOuts: [{ siret: siret(1) }, { siren: siren(2) }, { email: "CONTACT@fictif-3.test" }, { domain: "www.fictif-4.test" }],
    prospects: [dbRow(5, { do_not_contact: 1, status: "DO_NOT_CONTACT" })],
  }));
  assert.deepEqual(res.map((r) => r.kind), ["ERROR", "ERROR", "ERROR", "ERROR", "ERROR", "NEW"]);
  for (const r of res.slice(0, 5)) assert.match(r.reason, /Opposition/);
  const label = rowsOf(csvOf(["Nom", "SIREN", "Statut de suivi"], [["X", siren(8), "Ne plus contacter"]]));
  assert.match(analyze(label, ctx()).at(0)!.reason, /Opposition/);
  const amb = rowsOf(csvOf(["Nom", "SIREN"], [["Y", siren(9)]]));
  const allDnc = analyze(amb, ctx({ prospects: [dbRow(91, { siren: siren(9), siret: siret(9, 1), do_not_contact: 1 }), dbRow(92, { siren: siren(9), siret: siret(9, 2), do_not_contact: 1 })] }));
  assert.equal(allDnc[0].kind, "ERROR");
});

test("Réimport rejoué : empreinte connue = « déjà importée », 0 création, 0 modification", () => {
  const csv = nativeCsv([exportRow(1), exportRow(2, { siret: null })]);
  const rows = rowsOf(csv);
  // 1er passage : tout est nouveau.
  const first = analyze(rows, ctx());
  assert.deepEqual(countKinds(first).new, 2);
  // Après application : fiches créées (ici, l'utilisateur a même vidé le téléphone de la 1re depuis) + empreintes enregistrées.
  const created = [dbRow(101, { siren: siren(1), siret: siret(1), phone: null }), dbRow(102, { siren: siren(2), siret: null, phone: "0100000000" })];
  const replay = analyze(rows, ctx({ prospects: created, appliedHashes: { [rows[0].hash]: 101, [rows[1].hash]: 102 } }));
  assert.deepEqual(replay.map((r) => [r.kind, r.alreadyImported, r.changes.length]), [["EXISTING", true, 0], ["EXISTING", true, 0]]);
  const d = decide(replay, {}, new Set());
  assert.ok(d.ok);
  if (d.ok) assert.deepEqual(d.decisions.map((x) => x.op), ["none", "none"]);
  // Même fichier, empreinte inconnue (journal vidé) : le rapprochement par identité suffit à éviter tout doublon.
  const noJournal = analyze(rows, ctx({ prospects: created }));
  assert.equal(countKinds(noJournal).new, 0);
  // Empreinte stable : le libellé de statut et la date d'ajout recalculés à chaque export ne comptent pas.
  const later = rowsOf(nativeCsv([exportRow(1, { status: "CONTACTED", discovered_at: "2026-05-01T00:00:00Z" })]));
  assert.equal(later[0].hash, rows[0].hash);
});

test("Doublon d'identité dans le fichier et SIREN partagé entre lignes", () => {
  const rows = rowsOf(csvOf(["Nom", "SIREN", "SIRET"], [["A", siren(1), siret(1)], ["A bis", siren(1), siret(1)], ["Siège ?", siren(1), ""]]));
  const res = analyze(rows, ctx());
  assert.deepEqual(res.map((r) => r.kind), ["NEW", "ERROR", "AMBIGUOUS"]);
  assert.match(res[1].reason, /Doublon dans le fichier.*ligne 2/);
});

test("Décisions : ambiguïté non résolue bloquante, choix hors propositions refusé, lignes décochées ignorées", () => {
  const rows = rowsOf(csvOf(["Nom", "SIREN"], [["Amb", siren(2)], ["Nouvelle", siren(5)], ["Erreur", ""]]));
  const res = analyze(rows, ctx({ prospects: [dbRow(21, { siren: siren(2), siret: siret(2, 1) }), dbRow(22, { siren: siren(2), siret: siret(2, 2) })] }));
  assert.equal(decide(res, {}, new Set()).ok, false);
  assert.equal(decide(res, { 2: { action: "link", id: 999 } }, new Set()).ok, false);
  const ok = decide(res, { 2: { action: "link", id: 22 } }, new Set([3]));
  assert.ok(ok.ok);
  if (ok.ok) assert.deepEqual(ok.decisions.map((x) => [x.line, x.op, "outcome" in x ? x.outcome : ""]), [[2, "update", ""], [3, "none", "SKIPPED"], [4, "none", "ERROR"]]);
  const create = decide(res, { 2: { action: "create" } }, new Set());
  assert.ok(create.ok && create.decisions[0].op === "create");
  const ignore = decide(res, { 2: { action: "ignore" } }, new Set());
  assert.ok(ignore.ok && ignore.decisions[0].op === "none");
  assert.deepEqual(parseResolutions({ 2: { action: "link", id: 22 }, 3: { action: "drop" }, x: { action: "create" }, 4: { action: "link", id: -1 } }), { 2: { action: "link", id: 22 } });
  assert.deepEqual(parseResolutions("nope"), {});
});

test("Jeton d'aperçu : change si la base change entre l'aperçu et la confirmation", () => {
  const rows = rowsOf(csvOf(["Nom", "SIREN", "E-mail"], [["A", siren(1), "a@fictif.test"]]));
  const t1 = previewToken("f", analyze(rows, ctx({ prospects: [dbRow(1)] })));
  assert.equal(previewToken("f", analyze(rows, ctx({ prospects: [dbRow(1)] }))), t1);
  assert.notEqual(previewToken("f", analyze(rows, ctx({ prospects: [dbRow(1, { email: "x@fictif.test" })] }))), t1, "champ rempli entre-temps");
  assert.notEqual(previewToken("g", analyze(rows, ctx({ prospects: [dbRow(1)] }))), t1, "autre fichier");
});

test("Nouvelle fiche : source import CSV, étape IMPORTED (aucune campagne), « Non trouvé » ≠ aucun site, URL = site à vérifier", () => {
  const [nf] = rowsOf(nativeCsv([exportRow(1, { website_status: "NOT_FOUND", website_absence_confidence: 0.85, website_url: null })]));
  assert.equal(nf.data.siteNotFound, true);
  const v = createValues(nf.data, 7, "2026-10-18T12:00:00Z");
  assert.equal(v.website_status, null, "jamais NOT_FOUND sans vraie recherche");
  assert.equal(v.pipeline_stage, "IMPORTED");
  assert.equal(v.status, "DISCOVERED");
  assert.equal(JSON.parse(String(v.provenance)).import_csv.source, "import CSV");
  assert.equal(JSON.parse(String(v.provenance)).import_csv.campaign, "Campagne test");
  assert.ok(analyze([nf], ctx())[0].warnings.some((w) => /Non trouvé/.test(w)));
  const [site] = rowsOf(nativeCsv([exportRow(2)]));
  const s = createValues(site.data, 7, "2026-10-18T12:00:00Z");
  assert.equal(s.website_status, "UNCERTAIN", "site déclaré par le fichier : à vérifier, jamais confirmé sur parole");
  assert.equal(s.website_url, "https://fictif-2.test/");
  assert.equal(s.company_name, "SOCIETE FICTIVE 2");
  assert.equal(s.trade_name, "Atelier Fictif 2");
  assert.equal(s.activity_key, "plombiers");
  assert.ok(JSON.parse(String(s.social_links))[0].via === "import");
});

test("Valeurs ignorées avec avertissement (téléphone, e-mail, URL, réseaux, code postal Excel) sans refuser la ligne", () => {
  const [r] = rowsOf(csvOf(["Nom", "SIREN", "Téléphone", "E-mail", "URL du site", "Facebook", "Code postal"],
    [["X", siren(1), "12", "pas-un-mail", "javascript:alert(1)", "https://exemple.test/page", "1000"]]));
  assert.deepEqual(r.errors, []);
  assert.equal(r.data.phone, null);
  assert.equal(r.data.email, null);
  assert.equal(r.data.url, null);
  assert.deepEqual(r.data.socials, []);
  assert.equal(r.data.postal, "01000");
  assert.equal(r.warnings.length, 5);
});
