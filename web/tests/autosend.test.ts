import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { AUTO_DEFAULTS, FREEMAIL, autoIneligible, sanitizeAutoSend } from "../lib/autosend";

// fiche COMPLÈTE (lib/complete.ts) : métier connu, site confirmé, traitement terminé
const P = { email: "contact@garage-exemple.fr", email_kind: "GENERIC_BUSINESS", do_not_contact: 0, contacted_at: null, draft_message: "Bonjour,…",
  pipeline_stage: "DONE", activity_label: "Garages", website_status: "CONFIRMED", status: "QUALIFIED", is_chain: 0 };

test("envoi automatique désactivé par défaut et réglage borné", () => {
  assert.equal(AUTO_DEFAULTS.enabled, false);
  assert.equal(sanitizeAutoSend({ enabled: "true" })!.enabled, false);          // seul un vrai booléen active
  assert.deepEqual(sanitizeAutoSend({ enabled: true, daily_cap: 500, start_hour: 9, end_hour: 17 }), { enabled: true, personal_emails: true, daily_cap: 30, start_hour: 9, end_hour: 17 });
  assert.equal(sanitizeAutoSend({ enabled: true, start_hour: 17, end_hour: 9 }), null);
  assert.equal(sanitizeAutoSend(null), null);
});

test("seules les adresses professionnelles jamais contactées sont mises en file", () => {
  assert.equal(autoIneligible(P, false), null);
  assert.equal(autoIneligible({ ...P, email_kind: "PERSONAL_BUSINESS", email: "jean.dupont@garage-exemple.fr" }, false), null);
  assert.match(autoIneligible({ ...P, email: "garage@gmail.com", email_kind: "UNCERTAIN" }, false)!, /non professionnelle/);
  assert.match(autoIneligible({ ...P, email: "garage@orange.fr" }, false)!, /non professionnelle/);
  assert.match(autoIneligible({ ...P, do_not_contact: 1 }, false)!, /ne plus contacter/);
  assert.equal(autoIneligible({ ...P, contacted_at: "2026-10-01" }, false), "déjà contacté");
  assert.equal(autoIneligible({ ...P, draft_message: null }, false), "pas de brouillon");
  assert.equal(autoIneligible({ ...P, email: null }, false), "pas d'adresse e-mail");
});

test("adresses Gmail, Orange… acceptées avec le réglage personal_emails (activé par défaut)", () => {
  assert.equal(AUTO_DEFAULTS.personal_emails, true);
  assert.equal(sanitizeAutoSend({ enabled: true, personal_emails: false })!.personal_emails, false);
  assert.equal(autoIneligible({ ...P, email: "garage@gmail.com", email_kind: "UNCERTAIN" }, true), null);
  assert.equal(autoIneligible({ ...P, email: "garage@orange.fr", email_kind: "GENERIC_BUSINESS" }, true), null);
  assert.match(autoIneligible({ ...P, email: "garage@gmail.com", email_kind: null }, true)!, /non vérifiée/);
  assert.equal(autoIneligible({ ...P, email: null }, true), "pas d'adresse e-mail");
});

test("même liste de messageries gratuites que le worker", () => {
  const py = fs.readFileSync(path.resolve(__dirname, "..", "..", "..", "worker", "worker", "local", "contacts.py"), "utf8");
  const block = py.slice(py.indexOf("FREEMAIL = {"), py.indexOf("}", py.indexOf("FREEMAIL = {")));
  const worker = new Set([...block.matchAll(/"([^"]+)"/g)].map((m) => m[1]));
  assert.deepEqual([...FREEMAIL].sort(), [...worker].sort());
});

test("fiche incomplète ou adresse invalide : jamais en file d'envoi", () => {
  assert.match(autoIneligible({ ...P, activity_label: null }, true)!, /fiche incomplète : métier inconnu/);
  assert.match(autoIneligible({ ...P, website_status: "UNCERTAIN" }, true)!, /site à trancher/);
  assert.match(autoIneligible({ ...P, website_status: "NOT_FOUND", website_absence_confidence: 0.7 }, true)!, /site à trancher/);   // revérification pas faite
  assert.equal(autoIneligible({ ...P, website_status: "NOT_FOUND", website_absence_confidence: 0.7,
    provenance: JSON.stringify({ site_recheck: { status: "NOT_FOUND" } }) }, true), null);
  assert.match(autoIneligible({ ...P, pipeline_stage: "AUDIT" }, true)!, /traitement pas terminé/);
  assert.match(autoIneligible({ ...P, auto_state: "bounced" }, true)!, /adresse invalide/);
});
