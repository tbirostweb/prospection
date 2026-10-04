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

/** Adresse du client vue par le proxy (Traefik renseigne X-Real-Ip ; à défaut, dernière entrée de X-Forwarded-For). */
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
    if (ct !== "application/json") return { status: 415, reason: "content-type application/json requis" };
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
    { key: "Referrer-Policy", value: "no-referrer" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
    { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
    { key: "X-Robots-Tag", value: "noindex, nofollow" },
  ];
  if (!dev) h.push({ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" });
  return h;
}
