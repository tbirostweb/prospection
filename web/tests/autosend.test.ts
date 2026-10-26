import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { AUTO_DEFAULTS, FREEMAIL, autoIneligible, sanitizeAutoSend } from "../lib/autosend";

const P = { email: "contact@garage-exemple.fr", email_kind: "GENERIC_BUSINESS", do_not_contact: 0, contacted_at: null, draft_message: "Bonjour,…" };

test("envoi automatique désactivé par défaut et réglage borné", () => {
  assert.equal(AUTO_DEFAULTS.enabled, false);
  assert.equal(sanitizeAutoSend({ enabled: "true" })!.enabled, false);          // seul un vrai booléen active
  assert.deepEqual(sanitizeAutoSend({ enabled: true, daily_cap: 500, start_hour: 9, end_hour: 17 }), { enabled: true, daily_cap: 30, start_hour: 9, end_hour: 17 });
  assert.equal(sanitizeAutoSend({ enabled: true, start_hour: 17, end_hour: 9 }), null);
  assert.equal(sanitizeAutoSend(null), null);
});

test("seules les adresses professionnelles jamais contactées sont mises en file", () => {
  assert.equal(autoIneligible(P), null);
  assert.equal(autoIneligible({ ...P, email_kind: "PERSONAL_BUSINESS", email: "jean.dupont@garage-exemple.fr" }), null);
  assert.match(autoIneligible({ ...P, email: "garage@gmail.com", email_kind: "UNCERTAIN" })!, /non professionnelle/);
  assert.match(autoIneligible({ ...P, email: "garage@orange.fr" })!, /non professionnelle/);
  assert.match(autoIneligible({ ...P, do_not_contact: 1 })!, /ne plus contacter/);
  assert.equal(autoIneligible({ ...P, contacted_at: "2026-10-01" }), "déjà contacté");
  assert.equal(autoIneligible({ ...P, draft_message: null }), "pas de brouillon");
  assert.equal(autoIneligible({ ...P, email: null }), "pas d'adresse e-mail");
});

test("même liste de messageries gratuites que le worker", () => {
  const py = fs.readFileSync(path.resolve(__dirname, "..", "..", "..", "worker", "worker", "local", "contacts.py"), "utf8");
  const block = py.slice(py.indexOf("FREEMAIL = {"), py.indexOf("}", py.indexOf("FREEMAIL = {")));
  const worker = new Set([...block.matchAll(/"([^"]+)"/g)].map((m) => m[1]));
  assert.deepEqual([...FREEMAIL].sort(), [...worker].sort());
});
