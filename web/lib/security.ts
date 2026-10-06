// Contrôles de sécurité PURS (sans dépendance Next) : testables avec `npm test`, utilisés par le middleware et les routes API.
// Aucun secret n'est renvoyé au navigateur ni journalisé ici.

/** Longueur minimale exigée pour APP_PASSWORD : en dessous, l'accès est refusé (fail-closed) et un avertissement est journalisé. */
export const MIN_PASSWORD_LENGTH = 14;

/** Comparaison en temps constant (indépendante de la position du premier caractère différent). */
export function safeEqual(a: string, b: string): boolean {
  const enc = new TextEncoder();
  const x = enc.encode(a), y = enc.encode(b);
  let diff = x.length ^ y.length;
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

export type AuthConfig = { user?: string; password?: string };

/** Vrai si la configuration d'authentification est utilisable (identifiant présent, mot de passe assez long). */
export function authConfigured(cfg: AuthConfig): boolean {
  return !!cfg.user && !!cfg.password && cfg.password.length >= MIN_PASSWORD_LENGTH;
}

/** Vérifie un en-tête `Authorization: Basic …` contre la configuration. Fail-closed si la configuration est absente ou trop faible. */
export function checkBasicAuth(header: string | null, cfg: AuthConfig): boolean {
  if (!authConfigured(cfg)) return false;
  const bytes = new TextEncoder().encode(`${cfg.user}:${cfg.password}`);
  const expected = "Basic " + btoa(Array.from(bytes, (b) => String.fromCharCode(b)).join(""));
  return safeEqual(header ?? "", expected);
}

/** Vérifie un couple identifiant / mot de passe saisi dans le formulaire (temps constant, les deux comparaisons sont toujours faites). */
export function checkCredentials(user: unknown, password: unknown, cfg: AuthConfig): boolean {
  if (!authConfigured(cfg) || typeof user !== "string" || typeof password !== "string") return false;
  const okUser = safeEqual(user, cfg.user!);
  const okPass = safeEqual(password, cfg.password!);
  return okUser && okPass;
}

// ─── Session par cookie signé (formulaire /login) ────────────────────────────────────────────────────────────────
// Jeton « v1.<expiration en s>.<HMAC-SHA256 base64url> ». Clé dérivée par HMAC de APP_PASSWORD (+ APP_USER), ou de
// APP_SESSION_SECRET facultatif ; le mot de passe entre TOUJOURS dans la dérivation : le changer révoque toutes les sessions.
// Web Crypto uniquement : fonctionne dans le middleware (Edge) comme dans les routes (Node).

export const SESSION_COOKIE = "prospection_session";
export const SESSION_MAX_AGE_S = 30 * 24 * 3600;
const SESSION_LABEL = "prospection-session-v1";

function b64url(bytes: ArrayBuffer): string {
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function hmac(key: Uint8Array | string, msg: string): Promise<ArrayBuffer> {
  const enc = new TextEncoder();
  const raw = typeof key === "string" ? enc.encode(key) : key;
  const k = await crypto.subtle.importKey("raw", raw as BufferSource, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return crypto.subtle.sign("HMAC", k, enc.encode(msg));
}

async function sessionKey(cfg: AuthConfig, secret?: string): Promise<Uint8Array> {
  const base = secret && secret.length >= 32 ? secret : cfg.password!;
  return new Uint8Array(await hmac(base, `${SESSION_LABEL}\0${cfg.user}\0${cfg.password}`));
}

/** Crée un jeton de session (null si l'authentification n'est pas configurée). */
export async function createSessionToken(cfg: AuthConfig, nowMs = Date.now(), secret?: string): Promise<string | null> {
  if (!authConfigured(cfg)) return null;
  const exp = Math.floor(nowMs / 1000) + SESSION_MAX_AGE_S;
  const sig = b64url(await hmac(await sessionKey(cfg, secret), `v1.${exp}`));
  return `v1.${exp}.${sig}`;
}

/** Vérifie un jeton de session : format, signature (temps constant), expiration bornée. Fail-closed. */
export async function verifySessionToken(token: string | undefined | null, cfg: AuthConfig, nowMs = Date.now(), secret?: string): Promise<boolean> {
  if (!token || token.length > 200 || !authConfigured(cfg)) return false;
  const m = /^v1\.(\d{1,12})\.([A-Za-z0-9_-]{43})$/.exec(token);
  if (!m) return false;
  const exp = Number(m[1]);
  const now = Math.floor(nowMs / 1000);
  if (!(exp > now) || exp > now + SESSION_MAX_AGE_S + 60) return false;
  const expected = b64url(await hmac(await sessionKey(cfg, secret), `v1.${exp}`));
  return safeEqual(m[2], expected);
}

/** Chemin de retour après connexion : chemin RELATIF interne uniquement (pas de redirection ouverte), sinon « / ». */
export function safeNextPath(raw: unknown): string {
  if (typeof raw !== "string" || raw.length > 512) return "/";
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return "/";
  if (/[\u0000-\u001f\u007f]/.test(raw)) return "/";
  try {
    const u = new URL(raw, "http://interne.invalid");
    if (u.origin !== "http://interne.invalid") return "/";
    if (u.pathname === "/login" || u.pathname.startsWith("/api/")) return "/";
    return u.pathname + u.search;
  } catch { return "/"; }
}

/** Limiteur d'échecs d'authentification par client, mémoire BORNÉE (instance unique ; derrière plusieurs instances, ajouter une limite au proxy). */
export class FailureLimiter {
  private hits = new Map<string, { count: number; first: number; blockedUntil: number }>();
  constructor(
    readonly maxFailures = 10,
    readonly windowMs = 15 * 60_000,
    readonly blockMs = 15 * 60_000,
    readonly maxEntries = 10_000,
  ) {}

  /** Millisecondes restantes de blocage (0 = autorisé à tenter). */
  blockedFor(key: string, now = Date.now()): number {
    const h = this.hits.get(key);
    if (!h) return 0;
    if (h.blockedUntil > now) return h.blockedUntil - now;
    if (now - h.first > this.windowMs) this.hits.delete(key);
    return 0;
  }

  /** Enregistre un échec ; renvoie vrai si le client vient d'être bloqué. */
  fail(key: string, now = Date.now()): boolean {
    let h = this.hits.get(key);
    if (!h || now - h.first > this.windowMs) h = { count: 0, first: now, blockedUntil: 0 };
    h.count += 1;
    if (h.count >= this.maxFailures) h.blockedUntil = now + this.blockMs;
    this.hits.delete(key);
    this.hits.set(key, h);
    while (this.hits.size > this.maxEntries) {
      const oldest = this.hits.keys().next().value;
      if (oldest === undefined) break;
      this.hits.delete(oldest);
    }
    return h.blockedUntil > now;
  }

  success(key: string): void { this.hits.delete(key); }
  get size(): number { return this.hits.size; }
}

/** Plafond GLOBAL d'échecs d'authentification (tous clients confondus) : X-Real-Ip est falsifiable par un conteneur du réseau
 *  partagé, la limite par adresse ne suffit donc pas. 50 échecs en 15 min bloquent toute nouvelle tentative pendant 15 min. */
export const GLOBAL_MAX_FAILURES = 50;
export const GLOBAL_KEY = "*";

export type AuthLimiters = { perClient: FailureLimiter; global: FailureLimiter };

/** Limiteurs d'authentification PARTAGÉS (globalThis) entre le middleware (Basic) et /api/auth/login, créés une seule fois par processus. */
export function authLimiters(maxFailures = 10, blockMinutes = 15): AuthLimiters {
  const g = globalThis as unknown as { __authLimiters?: AuthLimiters };
  return (g.__authLimiters ??= {
    perClient: new FailureLimiter(maxFailures, 15 * 60_000, blockMinutes * 60_000),
    global: new FailureLimiter(GLOBAL_MAX_FAILURES, 15 * 60_000, 15 * 60_000, 1),
  });
}

/** Millisecondes de blocage restantes pour ce client (limite par adresse OU limite globale). */
export function authBlockedFor(l: AuthLimiters, key: string, now = Date.now()): number {
  return Math.max(l.perClient.blockedFor(key, now), l.global.blockedFor(GLOBAL_KEY, now));
}

/** Enregistre un échec dans les deux compteurs (par client et global). */
export function authFail(l: AuthLimiters, key: string, now = Date.now()): void {
  l.perClient.fail(key, now);
  l.global.fail(GLOBAL_KEY, now);
}

/** Adresse du client vue par le proxy (Traefik renseigne X-Real-Ip ; à défaut, dernière entrée de X-Forwarded-For).
 *  Next ne fournit pas l'adresse du pair TCP : impossible de vérifier ici que l'en-tête vient bien de Traefik. La limite
 *  globale (authLimiters) et un ratelimit Traefik (README) couvrent la falsification de cet en-tête. */
export function clientKey(headers: { get(name: string): string | null }): string {
  const real = headers.get("x-real-ip")?.trim();
  if (real) return real;
  const xff = headers.get("x-forwarded-for");
  if (xff) {
    const parts = xff.split(",").map((s) => s.trim()).filter(Boolean);
    if (parts.length) return parts[parts.length - 1];
  }
  return "unknown";
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const BODY_METHODS = new Set(["POST", "PUT", "PATCH"]);

/**
 * Garde anti-CSRF des mutations (l'authentification Basic est renvoyée automatiquement par le navigateur) :
 *  - Sec-Fetch-Site, s'il est présent, doit valoir same-origin (ou none : saisie directe) ;
 *  - Origin est OBLIGATOIRE et doit correspondre à l'hôte servi (ou à APP_URL) ;
 *  - POST/PUT/PATCH exigent Content-Type application/json (une requête « simple » text/plain ou formulaire est refusée).
 * Renvoie null si la requête est acceptée, sinon le motif du refus (403/415).
 */
export function checkMutation(
  method: string,
  headers: { get(name: string): string | null },
  appUrl?: string,
  allowForm = false,
): { status: 403 | 415; reason: string } | null {
  const m = method.toUpperCase();
  if (SAFE_METHODS.has(m)) return null;
  const site = headers.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "none") return { status: 403, reason: "cross-site" };
  const origin = headers.get("origin");
  if (!origin || origin === "null") return { status: 403, reason: "origin manquante" };
  let originHost: string;
  try { originHost = new URL(origin).host.toLowerCase(); } catch { return { status: 403, reason: "origin invalide" }; }
  const allowed = new Set<string>();
  const host = (headers.get("x-forwarded-host") ?? headers.get("host") ?? "").split(",")[0].trim().toLowerCase();
  if (host) allowed.add(host);
  if (appUrl) { try { allowed.add(new URL(appUrl).host.toLowerCase()); } catch { /* APP_URL invalide : ignorée */ } }
  if (!allowed.has(originHost)) return { status: 403, reason: "origin non autorisée" };
  if (BODY_METHODS.has(m)) {
    const ct = (headers.get("content-type") ?? "").split(";")[0].trim().toLowerCase();
    if (ct !== "application/json" && !(allowForm && ct === "application/x-www-form-urlencoded")) return { status: 415, reason: "content-type application/json requis" };
  }
  return null;
}

/** Identifiant entier strictement positif (sinon null). */
export function parseId(raw: unknown): number | null {
  const s = String(raw ?? "");
  if (!/^[1-9]\d{0,9}$/.test(s)) return null;
  const n = Number(s);
  return Number.isSafeInteger(n) && n <= 2_147_483_647 ? n : null;
}

/** URL affichable dans un lien : uniquement http(s), sinon null (bloque javascript:, data:, etc.). */
export function safeHref(raw: unknown): string | null {
  if (typeof raw !== "string" || !raw.trim()) return null;
  try {
    const u = new URL(raw.trim());
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch { return null; }
}

/** Échappe un texte destiné à du HTML brut (popups / tooltips Leaflet, qui interprètent le HTML). */
export function escapeHtml(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
}

export const MAX_JSON_BYTES = 64 * 1024;

/** Analyse un corps JSON borné et exige un OBJET simple (pas de tableau, pas de prototype exotique). */
export function parseJsonBody(text: string, maxBytes = MAX_JSON_BYTES):
  { ok: true; value: Record<string, any> } | { ok: false; status: 400 | 413; error: string } {
  if (new TextEncoder().encode(text).length > maxBytes) return { ok: false, status: 413, error: "corps de requête trop volumineux" };
  let v: unknown;
  try { v = text ? JSON.parse(text) : {}; } catch { return { ok: false, status: 400, error: "JSON invalide" }; }
  if (!v || typeof v !== "object" || Array.isArray(v)) return { ok: false, status: 400, error: "objet JSON attendu" };
  const out: Record<string, any> = Object.create(null);
  for (const [k, val] of Object.entries(v)) {
    if (k === "__proto__" || k === "constructor" || k === "prototype") continue;
    out[k] = val;
  }
  return { ok: true, value: out };
}

/** En-têtes de sécurité HTTP appliqués à toutes les réponses (next.config.js). */
export function securityHeaders(dev = false): { key: string; value: string }[] {
  const csp = [
    "default-src 'self'",
    // Next injecte des scripts inline d'hydratation ; Leaflet est chargé depuis cdnjs avec SRI.
    `script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com${dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com",
    "img-src 'self' data: blob: https://*.tile.openstreetmap.org https://tile.openstreetmap.org https://cdnjs.cloudflare.com",
    "font-src 'self' data:",
    `connect-src 'self' https://geo.api.gouv.fr${dev ? " ws: wss:" : ""}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
  const h = [
    { key: "Content-Security-Policy", value: csp },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "X-Frame-Options", value: "DENY" },
    // same-origin (et non no-referrer) : aucun référent envoyé aux sites tiers, mais un formulaire HTML (/login, déconnexion)
    // garde un en-tête Origin réel — avec no-referrer, le navigateur envoie « Origin: null » et la garde anti-CSRF le refuse.
    { key: "Referrer-Policy", value: "same-origin" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
    { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
    { key: "X-Robots-Tag", value: "noindex, nofollow" },
  ];
  if (!dev) h.push({ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" });
  return h;
}
