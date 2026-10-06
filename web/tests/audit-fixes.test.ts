import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  FailureLimiter, GLOBAL_KEY, GLOBAL_MAX_FAILURES, authBlockedFor, authFail, authLimiters, escapeHtml,
} from "../lib/security";

// Les tests sont compilés dans .test-dist/tests : la racine du projet web est deux niveaux au-dessus.
const WEB = join(__dirname, "..", "..");

test("F1 : N échecs depuis des IP toutes différentes déclenchent la limite globale (429)", () => {
  const l = { perClient: new FailureLimiter(10, 15 * 60_000, 15 * 60_000), global: new FailureLimiter(GLOBAL_MAX_FAILURES, 15 * 60_000, 15 * 60_000, 1) };
  for (let i = 0; i < GLOBAL_MAX_FAILURES - 1; i++) {
    const ip = `203.0.${i >> 8}.${i & 255}`;
    assert.equal(authBlockedFor(l, ip, 0), 0);
    authFail(l, ip, 0);
  }
  assert.equal(authBlockedFor(l, "198.51.100.1", 0), 0, "sous le plafond global : un nouveau client peut tenter");
  authFail(l, "198.51.100.2", 0);
  assert.ok(authBlockedFor(l, "198.51.100.3", 0) > 0, "plafond global atteint : tout client est bloqué");
  assert.ok(authBlockedFor(l, "198.51.100.3", 15 * 60_000 - 1) > 0);
  assert.equal(authBlockedFor(l, "198.51.100.3", 15 * 60_000 + 1), 0, "le blocage global expire");
});

test("F1 : la limite par client reste active et le succès d'un client ne remet pas le compteur global à zéro", () => {
  const l = { perClient: new FailureLimiter(3, 60_000, 60_000), global: new FailureLimiter(5, 60_000, 60_000, 1) };
  for (let i = 0; i < 3; i++) authFail(l, "1.1.1.1", 0);
  assert.ok(authBlockedFor(l, "1.1.1.1", 0) > 0);
  assert.equal(authBlockedFor(l, "2.2.2.2", 0), 0);
  l.perClient.success("2.2.2.2");
  authFail(l, "3.3.3.3", 0); authFail(l, "4.4.4.4", 0);
  assert.ok(l.global.blockedFor(GLOBAL_KEY, 0) > 0);
});

test("F1 : limiteurs partagés (même instance pour le middleware et /api/auth/login)", () => {
  assert.equal(authLimiters(), authLimiters(7, 3));
  assert.equal(authLimiters().global.maxFailures, GLOBAL_MAX_FAILURES);
});

test("F7 : un nom de campagne <img src=x onerror=alert(1)> est rendu en texte brut dans l'infobulle", () => {
  const name = "<img src=x onerror=alert(1)>";
  const out = escapeHtml(name);
  assert.equal(out, "&lt;img src=x onerror=alert(1)&gt;");
  assert.ok(!out.includes("<"));
  assert.equal(escapeHtml(`"'&`), "&quot;&#39;&amp;");
  assert.equal(escapeHtml(null), "");
  const src = readFileSync(join(WEB, "components", "MapView.tsx"), "utf8");
  for (const m of src.matchAll(/\.bindTooltip\(([^)]*)\)/g)) assert.match(m[1], /^esc\(/, `infobulle non échappée : ${m[0]}`);
  assert.match(src, /\.bindTooltip\(esc\(c\.name\)\)/);
});
