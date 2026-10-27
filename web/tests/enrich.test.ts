// État des passages d'enrichissement (lib/enrich.ts) : résumé lisible, mêmes sous-passages que le worker (worker/local/enrich.py).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { STEP_LABELS, STOP_LABELS, enrichSummary, parseSteps } from "../lib/enrich";

test("résumé : seulement ce qui a été traité, au singulier ou au pluriel", () => {
  assert.equal(enrichSummary([{ key: "places_site", done: 10, found: 2, stop: "limit" }, { key: "recheck", done: 1, found: 0, stop: "nothing" },
    { key: "email", done: 0, found: 0, stop: "lock" }]), "10 sites Google vérifiés (2 trouvés), 1 revérification de site");
  assert.equal(enrichSummary([]), "aucune fiche traitée");
  assert.deepEqual(parseSteps("pas du json"), []);
});

test("mêmes sous-passages et raisons d'arrêt que le worker", () => {
  const py = fs.readFileSync(path.resolve(__dirname, "..", "..", "..", "worker", "worker", "local", "enrich.py"), "utf8");
  const steps = (name: string) => (py.match(new RegExp(`${name} = \\(([^)]*)\\)`))?.[1] ?? "").match(/"([a-z_]+)"/g)!.map((s) => s.slice(1, -1));
  assert.deepEqual([...steps("FREE_STEPS"), ...steps("ENGINE_STEPS")].sort(), Object.keys(STEP_LABELS).sort());
  for (const code of ["nothing", "limit", "time", "quota", "engines", "lock", "error", "off"]) assert.ok(STOP_LABELS[code], code);
});
