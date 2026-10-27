// « Fiche complète » en SQL (lib/complete.ts completeSql / qualitySql) contre un VRAI MySQL : chaque cas partagé avec le worker est inséré, la
// requête doit donner le même verdict que la fonction. Activé seulement si TEST_DATABASE_URL est défini (voir scripts/dev_mysql.sh) ; sinon ignoré.
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "fs";
import * as path from "path";
import mysql from "mysql2/promise";
import { completeSql, ficheChannel, ficheMissing, qualitySql } from "../lib/complete";

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
