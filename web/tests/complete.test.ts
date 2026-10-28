// « Fiche complète » (lib/complete.ts) : mêmes cas que le worker (worker/tests/fixtures/fiche_complete_cases.json).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { CONTACT_REASONS, SITE_REASONS, contactReason, draftAngle, emailOk, ficheAngle, ficheChannel, ficheMissing, ficheReason, siteReason } from "../lib/complete";

export const CASES = JSON.parse(fs.readFileSync(path.resolve(__dirname, "..", "..", "..", "worker", "tests", "fixtures", "fiche_complete_cases.json"), "utf8"));

test("cas partagés avec le worker", () => {
  for (const c of CASES.cases) {
    const p = { ...CASES.base, ...c.p };
    assert.deepEqual(ficheMissing(p, c.personal), c.missing, c.name);
    assert.equal(ficheChannel(p, c.personal), c.channel, c.name);
    // l'e-mail d'abord : le site ne bloque plus l'envoi (il oriente le message), seuls les autres manques comptent
    assert.equal(ficheReason(p, c.personal) === null, c.missing.every((m: string) => m === "site") && emailOk(p, c.personal), c.name);
    assert.equal(ficheAngle(p), c.angle, `${c.name} : angle`);
    assert.equal(siteReason(p), c.site_reason, `${c.name} : raison du site`);
    assert.equal(contactReason(p, c.personal), c.contact_reason, `${c.name} : raison du contact`);
  }
});

test("mêmes libellés que le worker", () => {
  const py = fs.readFileSync(path.resolve(__dirname, "..", "..", "..", "worker", "worker", "local", "complete.py"), "utf8");
  for (const label of ["métier inconnu (activité non classée)", "site à trancher", "e-mail invalide", "aucun contact fiable", "traitement pas terminé"]) assert.ok(py.includes(label), label);
});

test("mêmes raisons et libellés que le worker", () => {
  const py = fs.readFileSync(path.resolve(__dirname, "..", "..", "..", "worker", "worker", "local", "complete.py"), "utf8");
  for (const [code, label] of [...Object.entries(SITE_REASONS), ...Object.entries(CONTACT_REASONS)]) {
    assert.ok(py.includes(`"${code}": "${label}"`), `${code} : ${label}`);
  }
});

test("angle du brouillon d'après son type (draft_kind coupé à 24 caractères) : miroir de draft_angle", () => {
  const cases: [string | null, string | null][] = [["sans_site", "creation"], ["ai_takeover_sans_site", "creation"], ["site_ameliorable", "refonte"],
    ["ai_takeover_site_amelior", "refonte"], ["site_refonte", "refonte"], ["ai_takeover_site_inacces", "refonte"], ["neutre", "neutre"],
    ["ai_takeover_neutre", "neutre"], [null, null], ["", null]];
  for (const [k, a] of cases) assert.equal(draftAngle(k), a, String(k));
});

test("site mis en doute juste avant l'envoi : plus vérifié, angle neutre", () => {
  const p = { ...CASES.base, provenance: { presend_identity: { doubt: true, url: CASES.base.website_url } } };
  assert.equal(ficheAngle(p), "neutre");
  assert.equal(ficheAngle({ ...p, site_review: "CONFIRMED" }), "refonte");                    // confirmé par toi : toujours vérifié
  assert.equal(ficheAngle({ ...p, provenance: { presend_identity: { doubt: true, url: "https://autre.test/" } } }), "refonte");
});
