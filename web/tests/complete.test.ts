// « Fiche complète » (lib/complete.ts) : mêmes cas que le worker (worker/tests/fixtures/fiche_complete_cases.json).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { ficheChannel, ficheMissing, ficheReason } from "../lib/complete";

export const CASES = JSON.parse(fs.readFileSync(path.resolve(__dirname, "..", "..", "..", "worker", "tests", "fixtures", "fiche_complete_cases.json"), "utf8"));

test("cas partagés avec le worker", () => {
  for (const c of CASES.cases) {
    const p = { ...CASES.base, ...c.p };
    assert.deepEqual(ficheMissing(p, c.personal), c.missing, c.name);
    assert.equal(ficheChannel(p, c.personal), c.channel, c.name);
    assert.equal(ficheReason(p, c.personal) === null, c.channel === "email", c.name);
  }
});

test("mêmes libellés que le worker", () => {
  const py = fs.readFileSync(path.resolve(__dirname, "..", "..", "..", "worker", "worker", "local", "complete.py"), "utf8");
  for (const label of ["métier inconnu (activité non classée)", "site à trancher", "e-mail invalide", "aucun contact fiable", "traitement pas terminé"]) assert.ok(py.includes(label), label);
});
