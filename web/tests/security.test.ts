import { test } from "node:test";
import assert from "node:assert/strict";
import {
  FailureLimiter, MIN_PASSWORD_LENGTH, SESSION_MAX_AGE_S, authConfigured, checkBasicAuth, checkCredentials, checkMutation, createSessionToken, safeNextPath, verifySessionToken, clientKey, parseId, parseJsonBody, safeEqual, safeHref, securityHeaders,
} from "../lib/security";

const H = (o: Record<string, string>) => ({ get: (k: string) => o[k.toLowerCase()] ?? null });
const basic = (u: string, p: string) => "Basic " + Buffer.from(`${u}:${p}`, "utf8").toString("base64");
const CFG = { user: "synthetique", password: "Mot-de-passe-de-test-long" };

test("safeEqual : égalité stricte, longueurs différentes refusées", () => {
  assert.equal(safeEqual("abc", "abc"), true);
  assert.equal(safeEqual("abc", "abd"), false);
  assert.equal(safeEqual("abc", "abcd"), false);
  assert.equal(safeEqual("", ""), true);
});

test("auth Basic : correcte acceptée, incorrecte / absente / mal formée refusée", () => {
  assert.equal(checkBasicAuth(basic(CFG.user, CFG.password), CFG), true);
  assert.equal(checkBasicAuth(basic(CFG.user, "mauvais-mot-de-passe-xx"), CFG), false);
  assert.equal(checkBasicAuth(null, CFG), false);
  assert.equal(checkBasicAuth("Bearer x", CFG), false);
});

test("auth Basic : caractères non ASCII (UTF-8) pris en charge", () => {
  const cfg = { user: "théo", password: "élévation-sécurisée-2026" };
  assert.equal(checkBasicAuth(basic(cfg.user, cfg.password), cfg), true);
});

test("auth fail-closed : configuration absente ou mot de passe trop court", () => {
  assert.equal(authConfigured({}), false);
  const short = { user: "u", password: "x".repeat(MIN_PASSWORD_LENGTH - 1) };
  assert.equal(authConfigured(short), false);
  assert.equal(checkBasicAuth(basic("u", short.password), short), false);
  assert.equal(checkBasicAuth(basic("", ""), { user: "", password: "" }), false);
});

test("limiteur : blocage après N échecs (429), remise à zéro sur succès, fenêtre expirée", () => {
  const l = new FailureLimiter(5, 1000, 2000, 100);
  for (let i = 0; i < 4; i++) assert.equal(l.fail("1.2.3.4", 0), false);
  assert.equal(l.blockedFor("1.2.3.4", 0), 0);
  assert.equal(l.fail("1.2.3.4", 0), true);
  assert.ok(l.blockedFor("1.2.3.4", 10) > 0);
  assert.equal(l.blockedFor("5.6.7.8", 10), 0, "un autre client n'est pas bloqué");
  assert.equal(l.blockedFor("1.2.3.4", 2001), 0, "blocage borné dans le temps");
  l.success("1.2.3.4");
  assert.equal(l.size, 0);
});

test("limiteur : mémoire bornée", () => {
  const l = new FailureLimiter(5, 60_000, 60_000, 50);
  for (let i = 0; i < 500; i++) l.fail(`10.0.${i >> 8}.${i & 255}`, 0);
  assert.ok(l.size <= 50);
});

test("clientKey : X-Real-Ip puis dernière entrée X-Forwarded-For", () => {
  assert.equal(clientKey(H({ "x-real-ip": "9.9.9.9", "x-forwarded-for": "1.1.1.1" })), "9.9.9.9");
  assert.equal(clientKey(H({ "x-forwarded-for": "6.6.6.6, 2.2.2.2" })), "2.2.2.2");
  assert.equal(clientKey(H({})), "unknown");
});

test("CSRF : GET libre ; mutation même origine JSON acceptée", () => {
  assert.equal(checkMutation("GET", H({})), null);
  assert.equal(checkMutation("POST", H({ host: "app.test", origin: "https://app.test", "content-type": "application/json; charset=utf-8", "sec-fetch-site": "same-origin" })), null);
  assert.equal(checkMutation("DELETE", H({ host: "app.test", origin: "https://app.test" })), null);
});

test("CSRF : origine tierce, origine absente, cross-site, text/plain refusés", () => {
  assert.equal(checkMutation("POST", H({ host: "app.test", origin: "https://evil.example", "content-type": "application/json" }))?.status, 403);
  assert.equal(checkMutation("PATCH", H({ host: "app.test", "content-type": "application/json" }))?.status, 403);
  assert.equal(checkMutation("POST", H({ host: "app.test", origin: "null", "content-type": "application/json" }))?.status, 403);
  assert.equal(checkMutation("DELETE", H({ host: "app.test", origin: "https://app.test", "sec-fetch-site": "cross-site" }))?.status, 403);
  assert.equal(checkMutation("POST", H({ host: "app.test", origin: "https://app.test", "content-type": "text/plain" }))?.status, 415);
  assert.equal(checkMutation("PUT", H({ host: "app.test", origin: "https://app.test", "content-type": "application/x-www-form-urlencoded" }))?.status, 415);
});

test("CSRF : APP_URL accepté comme origine", () => {
  assert.equal(checkMutation("POST", H({ host: "web:3000", origin: "https://prospection.example", "content-type": "application/json" }), "https://prospection.example"), null);
});

test("parseId : entiers positifs seulement", () => {
  assert.equal(parseId("42"), 42);
  for (const bad of ["0", "-1", "1.5", "1e3", "abc", "", "42 OR 1=1", "99999999999"]) assert.equal(parseId(bad), null, bad);
});

test("safeHref : http(s) uniquement", () => {
  assert.equal(safeHref("https://exemple.fr/a"), "https://exemple.fr/a");
  for (const bad of ["javascript:alert(1)", " JAVASCRIPT:alert(1)", "data:text/html,x", "vbscript:x", "", null, 3]) assert.equal(safeHref(bad), null, String(bad));
});

test("parseJsonBody : JSON invalide, tableau, trop gros, prototype", () => {
  assert.equal(parseJsonBody("{").ok, false);
  assert.deepEqual((parseJsonBody("{") as any).status, 400);
  assert.equal(parseJsonBody("[1]").ok, false);
  assert.equal((parseJsonBody("x".repeat(70_000)) as any).status, 413);
  const r = parseJsonBody('{"__proto__":{"admin":true},"a":1}');
  assert.ok(r.ok);
  if (r.ok) { assert.equal(r.value.a, 1); assert.equal((r.value as any).admin, undefined); assert.equal(({} as any).admin, undefined); }
  assert.ok(parseJsonBody("").ok);
});

test("en-têtes de sécurité : CSP stricte, HSTS en production, identiques à next.config.js", () => {
  const prod = securityHeaders(false);
  const csp = prod.find((h) => h.key === "Content-Security-Policy")!.value;
  assert.match(csp, /frame-ancestors 'none'/);
  assert.match(csp, /object-src 'none'/);
  assert.doesNotMatch(csp, /unsafe-eval/);
  assert.ok(prod.some((h) => h.key === "Strict-Transport-Security"));
  const prev = process.env.NODE_ENV;
  (process.env as any).NODE_ENV = "production";
  const cfg = require("../../next.config.js");
  (process.env as any).NODE_ENV = prev;
  return cfg.headers().then((rules: any[]) => {
    assert.deepEqual(rules[0].headers, prod);
  });
});

test("formulaire : identifiants corrects acceptés, incorrects / absents / config faible refusés", () => {
  assert.equal(checkCredentials(CFG.user, CFG.password, CFG), true);
  assert.equal(checkCredentials(CFG.user, CFG.password + "x", CFG), false);
  assert.equal(checkCredentials("autre", CFG.password, CFG), false);
  assert.equal(checkCredentials(null, null, CFG), false);
  assert.equal(checkCredentials("u", "court", { user: "u", password: "court" }), false);
});

test("session : jeton signé valide, falsifié, expiré, autre mot de passe refusés", async () => {
  const now = 1_800_000_000_000;
  const t = (await createSessionToken(CFG, now))!;
  assert.match(t, /^v1\.\d+\.[A-Za-z0-9_-]{43}$/);
  assert.equal(await verifySessionToken(t, CFG, now + 1000), true);
  assert.equal(await verifySessionToken(t, CFG, now + (SESSION_MAX_AGE_S + 1) * 1000), false, "expiré");
  const [v, exp, sig] = t.split(".");
  assert.equal(await verifySessionToken(`${v}.${Number(exp) + 3600}.${sig}`, CFG, now), false, "expiration modifiée");
  assert.equal(await verifySessionToken(`${v}.${exp}.${sig.slice(0, -1)}${sig.endsWith("A") ? "B" : "A"}`, CFG, now), false, "signature modifiée");
  assert.equal(await verifySessionToken(t, { ...CFG, password: CFG.password + "-change" }, now), false, "mot de passe changé = révocation");
  assert.equal(await verifySessionToken(t, { user: "u", password: "court" }, now), false, "fail-closed");
  assert.equal(await verifySessionToken("", CFG, now), false);
  assert.equal(await verifySessionToken(undefined, CFG, now), false);
  const secret = "s".repeat(40);
  const ts = (await createSessionToken(CFG, now, secret))!;
  assert.equal(await verifySessionToken(ts, CFG, now, secret), true);
  assert.equal(await verifySessionToken(ts, CFG, now), false, "APP_SESSION_SECRET pris en compte");
});

test("redirection après connexion : chemins internes uniquement", () => {
  assert.equal(safeNextPath("/local/42?tab=a"), "/local/42?tab=a");
  assert.equal(safeNextPath("/settings"), "/settings");
  for (const bad of ["//evil.example", "https://evil.example", "/\\evil.example", "\\evil", "javascript:alert(1)", "/login", "/api/auth/login",
    "/%0d%0aSet-Cookie:x", "/a\nb", "", null, 42, "/" + "a".repeat(600)]) {
    const r = safeNextPath(bad);
    assert.ok(r === "/" || (r.startsWith("/") && !r.startsWith("//")), String(bad));
    assert.ok(!/evil|login|api/.test(r), String(bad));
  }
});

test("CSRF : formulaire accepté seulement si autorisé (routes de connexion), origine toujours exigée", () => {
  const form = { host: "app.test", origin: "https://app.test", "content-type": "application/x-www-form-urlencoded" };
  assert.equal(checkMutation("POST", H(form), undefined, true), null);
  assert.equal(checkMutation("POST", H(form))?.status, 415);
  assert.equal(checkMutation("POST", H({ ...form, origin: "https://evil.example" }), undefined, true)?.status, 403);
  assert.equal(checkMutation("POST", H({ host: "app.test", "content-type": "application/x-www-form-urlencoded" }), undefined, true)?.status, 403);
  assert.equal(checkMutation("POST", H({ ...form, "content-type": "multipart/form-data; boundary=x" }), undefined, true)?.status, 415);
});
