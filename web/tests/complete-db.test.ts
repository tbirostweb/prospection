// « Fiche complète » en SQL (lib/complete.ts completeSql / qualitySql) contre un VRAI MySQL : chaque cas partagé avec le worker est inséré, la
// requête doit donner le même verdict que la fonction. Activé seulement si TEST_DATABASE_URL est défini (voir scripts/dev_mysql.sh) ; sinon ignoré.
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "fs";
import * as path from "path";
import mysql from "mysql2/promise";
import { CONTACT_REASONS, SITE_REASONS, completeSql, contactReason, ficheChannel, ficheMissing, qualitySql, siteReason } from "../lib/complete";
import { DAY_RUNS_SQL, LAST_FULL_RUN_SQL, LAST_RUN_SQL, enrichSummary, parseSteps } from "../lib/enrich";

const BASE = process.env.TEST_DATABASE_URL;
const DB = "prospection_complete_test";
const ROOT = path.resolve(__dirname, "..", "..", "..");
const skip = !BASE ? "TEST_DATABASE_URL non défini (voir scripts/dev_mysql.sh)" : false;
const CASES = JSON.parse(fs.readFileSync(path.join(ROOT, "worker", "tests", "fixtures", "fiche_complete_cases.json"), "utf8"));
let conn: mysql.Connection;

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
  await conn.query("DELETE FROM local_prospects");
});
after(async () => { if (conn) { await conn.query(`DROP DATABASE IF EXISTS ${DB}`); await conn.end(); } });

async function insertCases() {
  await conn.query("DELETE FROM local_prospects");
  let id = 0;
  for (const c of CASES.cases) {
    const p: Record<string, any> = { ...CASES.base, ...c.p };
    id += 1;
    const row = { id, siret: String(30000000000000 + id), fingerprint: `fp-${id}`, company_name: `FICTIVE ${id}`, ...p,
      provenance: p.provenance == null ? null : JSON.stringify(p.provenance), bodacc: p.bodacc == null ? null : JSON.stringify(p.bodacc) };
    await conn.query(`INSERT INTO local_prospects (${Object.keys(row).join(",")}) VALUES (?)`, [Object.values(row)]);
  }
}

test("SQL et fonction donnent le même verdict sur chaque cas", { skip }, async () => {
  await insertCases();
  for (const personal of [false, true]) {
    const s = completeSql(personal);
    const rows = await conn.query(`SELECT p.id, ${s.complete} AS complete, ${s.email} AS email FROM local_prospects p ORDER BY p.id`).then((r) => r[0] as any[]);
    CASES.cases.forEach((c: any, i: number) => {
      const p = { ...CASES.base, ...c.p };
      const ch = ficheChannel(p, personal);
      assert.equal(Number(rows[i].complete) === 1, ch !== null, `${c.name} (personal=${personal})`);
      if (ch !== null) assert.equal(Number(rows[i].email) === 1, ch === "email", `${c.name} e-mail (personal=${personal})`);
    });
  }
});

test("bloc « Qualité de la liste » : requête exécutée, comptes cohérents", { skip }, async () => {
  await insertCases();
  const [q] = (await conn.query(qualitySql(true)))[0] as any[];
  const pertinent = CASES.cases.map((c: any) => ({ ...CASES.base, ...c.p })).filter((p: any) => !ficheMissing(p, true).includes("entreprise"));
  assert.equal(Number(q.total), pertinent.length);
  assert.equal(Number(q.complete_email), pertinent.filter((p: any) => ficheChannel(p, true) === "email").length);
  assert.equal(Number(q.complete_phone), pertinent.filter((p: any) => ficheChannel(p, true) === "phone").length);
  assert.equal(Number(q.no_metier), pertinent.filter((p: any) => ficheMissing(p, true).includes("metier")).length);
  assert.equal(Number(q.site_todo), pertinent.filter((p: any) => ficheMissing(p, true).includes("site")).length);
  assert.equal(Number(q.no_contact), pertinent.filter((p: any) => ficheMissing(p, true).includes("contact")).length);
  assert.equal(Number(q.email_invalid), pertinent.filter((p: any) => ficheMissing(p, true).includes("email_invalide")).length);
  assert.equal(Number(q.processing), pertinent.filter((p: any) => ficheMissing(p, true).includes("traitement")).length);
});

test("raisons en SQL : même raison que siteReason / contactReason sur chaque cas", { skip }, async () => {
  await insertCases();
  for (const personal of [false, true]) {
    const s = completeSql(personal);
    const rows = await conn.query(`SELECT p.id, ${s.siteReason} AS site, ${s.contactReason} AS contact FROM local_prospects p ORDER BY p.id`).then((r) => r[0] as any[]);
    CASES.cases.forEach((c: any, i: number) => {
      const p = { ...CASES.base, ...c.p };
      assert.equal(rows[i].site, siteReason(p), `${c.name} : site (personal=${personal})`);
      assert.equal(rows[i].contact, contactReason(p, personal), `${c.name} : contact (personal=${personal})`);
    });
  }
});

test("bloc « Qualité de la liste » : ventilations cohérentes", { skip }, async () => {
  await insertCases();
  const [q] = (await conn.query(qualitySql(true)))[0] as any[];
  const pertinent = CASES.cases.map((c: any) => ({ ...CASES.base, ...c.p })).filter((p: any) => !ficheMissing(p, true).includes("entreprise"));
  const incomplete = pertinent.filter((p: any) => ficheChannel(p, true) === null);
  for (const code of Object.keys(SITE_REASONS)) assert.equal(Number(q[`site_${code}`]), incomplete.filter((p: any) => siteReason(p) === code).length, code);
  for (const code of ["refused", "form", "none"]) assert.equal(Number(q[`contact_${code}`]), incomplete.filter((p: any) => contactReason(p, true) === code).length, code);
  const sites = Object.keys(SITE_REASONS).reduce((a, c) => a + Number(q[`site_${c}`]), 0);
  assert.equal(sites, Number(q.site_todo));                                       // chaque « site à trancher » a une raison
  assert.equal(Number(q.contact_refused) + Number(q.contact_form) + Number(q.contact_none), Number(q.no_contact));
  assert.ok(Object.keys(CONTACT_REASONS).includes("invalid"));
  for (const k of ["processing_campaign", "processing_abandoned", "processing_imported"]) assert.ok(k in q, k);
});

test("état des passages d'enrichissement : requêtes exécutées", { skip }, async () => {
  await conn.query("DELETE FROM local_enrich_runs");
  await conn.query(`INSERT INTO local_enrich_runs (started_at, duration_ms, mode, stopped, steps) VALUES
    (UTC_TIMESTAMP() - INTERVAL 40 MINUTE, 120000, 'full', 'verrou pris : une campagne tournait', ?),
    (UTC_TIMESTAMP() - INTERVAL 10 MINUTE, 30000, 'no_engines', 'rien d''autre à faire', ?)`,
    [JSON.stringify([{ key: "places_site", done: 10, found: 3, stop: "limit" }, { key: "email", done: 0, found: 0, stop: "lock" }]),
     JSON.stringify([{ key: "site_guess", done: 4, found: 0, stop: "nothing" }])]);
  const [last] = (await conn.query(LAST_RUN_SQL))[0] as any[];
  const [full] = (await conn.query(LAST_FULL_RUN_SQL))[0] as any[];
  const [day] = (await conn.query(DAY_RUNS_SQL))[0] as any[];
  assert.equal(last.mode, "no_engines");
  assert.equal(enrichSummary(parseSteps(full.steps)), "10 sites Google vérifiés (3 trouvés)");
  assert.deepEqual([Number(day.runs), Number(day.locked), Number(day.skipped)], [2, 1, 0]);
});
