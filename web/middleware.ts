import { NextRequest, NextResponse } from "next/server";
import { FailureLimiter, authConfigured, checkBasicAuth, checkMutation, clientKey } from "./lib/security";

// Protège TOUTES les pages et API par HTTP Basic Auth (app perso mono-utilisateur).
// Identifiants dans les variables d'env APP_USER / APP_PASSWORD (onglet Dokploy) ; APP_PASSWORD : 14 caractères minimum.
// Fail-closed : si non configuré (ou mot de passe trop court), l'accès est refusé.
// En plus : limitation des échecs par client (429) et garde anti-CSRF sur toutes les mutations (Origin + JSON).
// Second facteur : à placer devant l'application au niveau du proxy (voir docs/SECURITE.md).
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|robots.txt).*)"],
};

// Aperçu LOCAL sans mot de passe (vérification visuelle automatique) : les TROIS conditions sont exigées — serveur de développement
// (`next dev`, jamais un build de production), DEV_NO_AUTH=1, et requête adressée à localhost / 127.0.0.1 (en-tête Host réel : en
// développement, `nextUrl.hostname` vaut toujours « localhost », quel que soit l'appelant).
function localPreview(req: NextRequest): boolean {
  const host = (req.headers.get("host") ?? "").replace(/:\d+$/, "").toLowerCase();
  return process.env.NODE_ENV === "development" && process.env.DEV_NO_AUTH === "1" && ["localhost", "127.0.0.1"].includes(host);
}

const limiter = new FailureLimiter(
  Number(process.env.AUTH_MAX_FAILURES) || 10,
  15 * 60_000,
  (Number(process.env.AUTH_BLOCK_MINUTES) || 15) * 60_000,
);
let warnedWeak = false;

/** Les réponses produites ici (401/403/429) ne passent pas par les en-têtes de next.config.js : on pose l'essentiel. */
function harden(res: NextResponse): NextResponse {
  res.headers.set("X-Content-Type-Options", "nosniff");
  res.headers.set("X-Frame-Options", "DENY");
  res.headers.set("Cache-Control", "no-store");
  return res;
}

export function middleware(req: NextRequest) {
  if (!localPreview(req)) {
    const cfg = { user: process.env.APP_USER, password: process.env.APP_PASSWORD };
    if (!authConfigured(cfg) && !warnedWeak) {
      warnedWeak = true;
      console.warn("[auth] APP_USER/APP_PASSWORD absents ou APP_PASSWORD < 14 caractères : accès refusé (fail-closed).");
    }
    const key = clientKey(req.headers);
    const wait = limiter.blockedFor(key);
    if (wait > 0) {
      return harden(new NextResponse("Trop de tentatives, réessayez plus tard", {
        status: 429, headers: { "Retry-After": String(Math.ceil(wait / 1000)) },
      }));
    }
    if (!checkBasicAuth(req.headers.get("authorization"), cfg)) {
      // Un navigateur envoie d'abord une requête sans identifiants : seule une tentative AVEC identifiants compte comme un échec.
      if (req.headers.get("authorization")) limiter.fail(key);
      return harden(new NextResponse("Authentification requise", {
        status: 401,
        headers: { "WWW-Authenticate": 'Basic realm="Prospection", charset="UTF-8"' },
      }));
    }
    limiter.success(key);
  }
  const refused = checkMutation(req.method, req.headers, process.env.APP_URL);
  if (refused) return harden(NextResponse.json({ error: "requête refusée" }, { status: refused.status }));
  return NextResponse.next();
}
