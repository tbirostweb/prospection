import { test } from "node:test";
import assert from "node:assert/strict";
import { splitByTrigger, strongSignals } from "../lib/local";

const TAKEOVER = { code: "takeover", label: "Reprise d'un commerce (BODACC)", strength: 1.0 };
const DOWN = { code: "site_down", label: "Site en panne", strength: 0.8 };

test("déclencheur fort : seuil 0,5, jamais « sans formulaire », le plus fort en tête", () => {
  const p = { buy_signals: JSON.stringify([{ code: "no_contact_way", label: "x", strength: 0.9 }, { code: "site_builder", label: "Wix", strength: 0.35 },
    { code: "socials_only", label: "Réseaux", strength: 0.5 }, TAKEOVER]) };
  assert.deepEqual(strongSignals(p).map((s) => s.code), ["takeover", "socials_only"]);
  assert.deepEqual(strongSignals({ buy_signals: null }), []);
  assert.deepEqual(strongSignals({ buy_signals: "{illisible" }), []);
});

test("file « À contacter » : déclencheurs forts d'abord, ordre d'origine gardé pour les autres", () => {
  const rows = [{ id: 1, buy_signals: [] }, { id: 2, buy_signals: [DOWN] }, { id: 3, buy_signals: null }, { id: 4, buy_signals: [TAKEOVER] }];
  const { triggered, others } = splitByTrigger(rows);
  assert.deepEqual(triggered.map((r) => r.id), [4, 2]);
  assert.deepEqual(others.map((r) => r.id), [1, 3]);
});
