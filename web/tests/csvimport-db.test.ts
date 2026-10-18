// Import CSV contre un VRAI MySQL (les faux exécuteurs ne voient pas les erreurs SQL). Activé seulement si TEST_DATABASE_URL est défini
// (ex. `TEST_DATABASE_URL=mysql://root@127.0.0.1:33099 npm test`, voir scripts/dev_mysql.sh) ; sinon ignoré.
// Base jetable construite comme en production : db/migrations (schéma + seed) puis toutes les migrations db/updates (dont 026).
// Données 100 % FICTIVES.
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "fs";
import * as path from "path";
import mysql from "mysql2/promise";
import { toCsv } from "../lib/export";
import { Q, confirmImport, previewImport } from "../lib/csvimport-db";

const BASE = process.env.TEST_DATABASE_URL;
const DB = "prospection_import_test";
const ROOT = path.resolve(__dirname, "..", "..", "..");      // .test-dist/tests → racine du dépôt
const skip = !BASE ? "TEST_DATABASE_URL non défini (voir scripts/dev_mysql.sh)" : false;
let conn: mysql.Connection;

const enc = (s: string) => new TextEncoder().encode(s);
const siren = (n: number) => String(200000000 + n);
const siret = (n: number, e = 1) => siren(n) + String(e).padStart(5, "0");
const q: Q = async (sql, params = []) => (await conn.query(sql, params))[0] as any;
/** Même contrat que lib/db.transaction : COMMIT si tout passe, ROLLBACK sinon. */
async function tx<T>(fn: (q: Q) => Promise<T>, wrap: (q: Q) => Q = (x) => x): Promise<T> {
  await conn.beginTransaction();
  try { const out = await fn(wrap(q)); await conn.commit(); return out; } catch (e) { await conn.rollback(); throw e; }
}
const count = async (table: string) => Number((await q(`SELECT COUNT(*) AS n FROM ${table}`))[0].n);

before(async () => {
  if (!BASE) return;
  const u = new URL(BASE);
  const opts = { host: u.hostname, port: Number(u.port || 3306), user: decodeURIComponent(u.username || "root"), password: decodeURIComponent(u.password),
    multipleStatements: true, timezone: "Z" as const };
  const admin = await mysql.createConnection(opts);
  await admin.query(`DROP DATABASE IF EXISTS ${DB}; CREATE DATABASE ${DB} CHARACTER SET utf8mb4`);
  await admin.end();
  conn = await mysql.createConnection({ ...opts, database: DB });
  for (const dir of ["db/migrations", "db/updates"]) {
    for (const f of fs.readdirSync(path.join(ROOT, dir)).filter((x) => x.endsWith(".sql")).sort()) await conn.query(fs.readFileSync(path.join(ROOT, dir, f), "utf8"));
  }
  await conn.query("SELECT 1 FROM local_imports, local_import_rows LIMIT 1");   // migration 026 appliquée
  await conn.query("DELETE FROM local_prospects");
  const ins = (o: Record<string, unknown>) => conn.query(`INSERT INTO local_prospects (${Object.keys(o).join(",")}) VALUES (?)`, [Object.values(o)]);
  // Fiche TRAVAILLÉE : statut, notes, brouillon, dates, relances, réponse — rien de tout cela ne doit bouger.
  await ins({ id: 1, siren: siren(1), siret: siret(1), fingerprint: "fp-1", company_name: "SOCIETE FICTIVE 1", status: "CONTACTED", category: "A_CONTACTER",
    notes: "note privée", draft_subject: "Objet", draft_message: "Brouillon", draft_created_at: "2026-09-01 10:00:00", contacted_at: "2026-09-02 08:00:00",
    last_contacted_at: "2026-09-10 08:00:00", followups: 2, last_followup_at: "2026-09-10 08:00:00", response_status: "NO_ANSWER", phone: "0100000001",
    pipeline_stage: "DONE" });
  // Deux établissements d'un même SIREN (ambiguïté), une fiche en opposition, une fiche sans date de contact.
  await ins({ id: 2, siren: siren(2), siret: siret(2, 1), fingerprint: "fp-2a", company_name: "FICTIVE DEUX", city: "Ville A" });
  await ins({ id: 3, siren: siren(2), siret: siret(2, 2), fingerprint: "fp-2b", company_name: "FICTIVE DEUX", city: "Ville B" });
  await ins({ id: 4, siren: siren(4), siret: siret(4), fingerprint: "fp-4", company_name: "FICTIVE OPPOSEE", do_not_contact: 1, status: "DO_NOT_CONTACT" });
  await ins({ id: 5, siren: siren(5), siret: siret(5), fingerprint: "fp-5", company_name: "FICTIVE CINQ", status: "QUALIFIED" });
  await conn.query("INSERT INTO local_do_not_contact (siren, reason) VALUES (?, 'test')", [siren(6)]);
});
after(async () => { if (conn) { await conn.query(`DROP DATABASE IF EXISTS ${DB}`); await conn.end(); } });

const HEADERS = ["Nom", "Raison sociale", "Téléphone", "E-mail", "URL du site", "Statut de suivi", "Contacté le", "SIREN", "SIRET"];
const FILE = toCsv(HEADERS, [
  ["Atelier Un", "SOCIETE FICTIVE 1", "01 00 00 00 99", "un@fictif.test", "", "Contacté", "2026-08-01", siren(1), siret(1)],  // existante travaillée
  ["Fictive Deux", "", "", "deux@fictif.test", "", "", "", siren(2), ""],                                                      // ambiguë
  ["Opposée", "", "", "", "", "", "", siren(4), siret(4)],                                                                     // opposition (fiche)
  ["Cinq", "", "", "commun@fictif.test", "", "À contacter", "2026-07-14", siren(5), siret(5)],                                 // datée, statut inchangé
  ["Liste rouge", "", "", "", "", "", "", siren(6), ""],                                                                       // opposition (liste)
  ["Nouvelle", "SOCIETE NOUVELLE", "01 00 00 00 07", "commun@fictif.test", "https://nouvelle.test/", "À contacter", "", siren(7), siret(7)],
]);

test("Aperçu : aucune écriture ; confirmation : uniquement les lignes confirmées, suivi préservé, journal ; réimport sans doublon", { skip }, async () => {
  const bytes = enc(FILE);
  const before = { p: await count("local_prospects"), i: await count("local_imports") };
  const pv = await previewImport(q, bytes, "fictif.csv");
  assert.deepEqual([await count("local_prospects"), await count("local_imports")], [before.p, before.i], "l'aperçu n'écrit rien");
  assert.deepEqual(pv.rows.map((r) => r.kind), ["EXISTING", "AMBIGUOUS", "ERROR", "EXISTING", "ERROR", "NEW"]);
  assert.ok(pv.rows[5].warnings.some((w) => /autre ligne du fichier/.test(w)), "e-mail partagé signalé");

  await assert.rejects(tx((t) => confirmImport(t, { bytes, filename: "fictif.csv", token: pv.token, resolutions: {}, skip: new Set() })), /ambiguïté/);
  assert.equal(await count("local_imports"), before.i, "refus : rien d'écrit");
  await assert.rejects(tx((t) => confirmImport(t, { bytes, filename: "fictif.csv", token: "périmé", resolutions: { 3: { action: "link", id: 3 } }, skip: new Set() })), /aperçu/);

  const sum = await tx((t) => confirmImport(t, { bytes, filename: "fictif.csv", token: pv.token, resolutions: { 3: { action: "link", id: 3 } }, skip: new Set() }));
  assert.deepEqual([sum.created, sum.updated, sum.unchanged, sum.ignored, sum.errors], [1, 3, 0, 0, 2]);

  const [p1] = await q("SELECT * FROM local_prospects WHERE id=1");
  assert.equal(p1.status, "CONTACTED");
  assert.equal(p1.category, "A_CONTACTER");
  assert.equal(p1.notes, "note privée");
  assert.equal(p1.draft_message, "Brouillon");
  assert.equal(p1.followups, 2);
  assert.equal(p1.response_status, "NO_ANSWER");
  assert.equal(p1.phone, "0100000001", "téléphone existant jamais écrasé");
  assert.equal(p1.email, "un@fictif.test", "e-mail vide complété");
  assert.equal(new Date(p1.contacted_at).toISOString(), "2026-09-02T08:00:00.000Z", "date existante conservée");
  assert.equal(new Date(p1.last_contacted_at).toISOString(), "2026-09-10T08:00:00.000Z");
  const [p3] = await q("SELECT email, city FROM local_prospects WHERE id=3");
  assert.equal(p3.email, "deux@fictif.test", "établissement choisi complété");
  assert.equal((await q("SELECT email FROM local_prospects WHERE id=2"))[0].email, null, "l'autre établissement n'est pas touché");
  const [p4] = await q("SELECT email, phone, status FROM local_prospects WHERE id=4");
  assert.deepEqual([p4.email, p4.phone, p4.status], [null, null, "DO_NOT_CONTACT"], "opposition jamais mise à jour");
  const [p5] = await q("SELECT status, contacted_at FROM local_prospects WHERE id=5");
  assert.equal(p5.status, "QUALIFIED", "statut inchangé malgré la date et le libellé « À contacter »");
  assert.equal(new Date(p5.contacted_at).toISOString(), "2026-07-14T10:00:00.000Z");
  assert.equal(Number((await q("SELECT COUNT(*) AS n FROM local_prospects WHERE siren=?", [siren(6)]))[0].n), 0, "opposition (liste) jamais créée");
  const [n] = await q("SELECT * FROM local_prospects WHERE siret=?", [siret(7)]);
  assert.equal(n.status, "DISCOVERED");
  assert.equal(n.pipeline_stage, "IMPORTED");
  assert.equal(n.website_status, "UNCERTAIN");
  assert.equal(n.category, null);
  assert.equal(n.company_name, "SOCIETE NOUVELLE");
  assert.equal(n.trade_name, "Nouvelle");
  assert.equal(Number((await q("SELECT COUNT(*) AS n FROM local_prospect_campaigns WHERE prospect_id=?", [n.id]))[0].n), 0, "aucune campagne : le worker ne la retraite pas");
  assert.equal((await q("SELECT source FROM local_prospect_sources WHERE prospect_id=?", [n.id]))[0].source, "csv_import");
  const journal = await q("SELECT line_no, outcome, row_hash FROM local_import_rows WHERE import_id=? ORDER BY line_no", [sum.importId]);
  assert.deepEqual(journal.map((j: any) => j.outcome), ["UPDATED", "UPDATED", "ERROR", "UPDATED", "ERROR", "CREATED"]);
  assert.ok(journal.every((j: any) => /^[0-9a-f]{64}$/.test(j.row_hash)), "journal : empreintes seulement");

  // Réimport du MÊME fichier : 0 création, 0 modification ; l'ancienne ambiguïté est reconnue par son empreinte.
  const snapshot = JSON.stringify(await q("SELECT * FROM local_prospects ORDER BY id"));
  const again = await previewImport(q, bytes, "fictif.csv");
  assert.equal(again.previous?.id, sum.importId, "fichier déjà importé signalé");
  assert.deepEqual(again.rows.map((r) => r.kind), ["EXISTING", "EXISTING", "ERROR", "EXISTING", "ERROR", "EXISTING"]);
  assert.ok(again.rows.filter((r) => r.kind === "EXISTING").every((r) => r.alreadyImported && r.changes.length === 0));
  const sum2 = await tx((t) => confirmImport(t, { bytes, filename: "fictif.csv", token: again.token, resolutions: {}, skip: new Set() }));
  assert.deepEqual([sum2.created, sum2.updated, sum2.unchanged], [0, 0, 4]);
  assert.equal(JSON.stringify(await q("SELECT * FROM local_prospects ORDER BY id")), snapshot, "aucune fiche modifiée");
});

test("Transaction : une erreur en cours d'import annule TOUT (aucune fiche, aucun journal)", { skip }, async () => {
  const bytes = enc(toCsv(["Nom", "SIREN", "SIRET", "E-mail"], [["Rollback A", siren(8), siret(8), ""], ["Rollback B", siren(1), siret(1), ""]]));
  await q("UPDATE local_prospects SET email=NULL WHERE id=1");
  const pv = await previewImport(q, bytes, "rollback.csv");
  const before = { p: JSON.stringify(await q("SELECT * FROM local_prospects ORDER BY id")), i: await count("local_imports"), r: await count("local_import_rows") };
  const failing = (inner: Q): Q => async (sql, params) => {
    if (/INSERT INTO local_import_rows/.test(sql)) throw Object.assign(new Error("panne simulée"), { code: "ER_SIMULATED" });
    return inner(sql, params);
  };
  await assert.rejects(tx((t) => confirmImport(t, { bytes, filename: "rollback.csv", token: pv.token, resolutions: {}, skip: new Set() }), failing), /panne simulée/);
  assert.equal(JSON.stringify(await q("SELECT * FROM local_prospects ORDER BY id")), before.p, "fiche créée puis annulée");
  assert.deepEqual([await count("local_imports"), await count("local_import_rows")], [before.i, before.r]);
});

test("Ligne décochée dans l'aperçu : jamais appliquée", { skip }, async () => {
  const bytes = enc(toCsv(["Nom", "SIREN", "SIRET"], [["Décochée", siren(9), siret(9)]]));
  const pv = await previewImport(q, bytes, "skip.csv");
  const sum = await tx((t) => confirmImport(t, { bytes, filename: "skip.csv", token: pv.token, resolutions: {}, skip: new Set([2]) }));
  assert.deepEqual([sum.created, sum.ignored], [0, 1]);
  assert.equal(Number((await q("SELECT COUNT(*) AS n FROM local_prospects WHERE siren=?", [siren(9)]))[0].n), 0);
});
