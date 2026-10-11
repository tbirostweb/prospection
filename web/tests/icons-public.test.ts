import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// Le matcher du middleware doit laisser publics les icônes (et seulement elles) ; tout le reste reste protégé.
const src = readFileSync(join(process.cwd(), "middleware.ts"), "utf8");
const m = /matcher:\s*\["([^"]+)"\]/.exec(src);
assert.ok(m, "matcher introuvable");
const re = new RegExp("^" + m![1].replace(/\\\\/g, "\\") + "$");
const protectedPath = (p: string) => re.test(p);

test("icônes publiques (hors middleware)", () => {
  for (const p of ["/icon.svg", "/apple-icon.png", "/favicon.ico"]) assert.equal(protectedPath(p), false, p);
});

test("le reste reste protégé par le middleware", () => {
  for (const p of ["/", "/local", "/api/settings", "/login", "/icon.png", "/apple-icon.svg", "/x/icon.svg", "/icon-secret.svg"])
    assert.equal(protectedPath(p), true, p);
});
