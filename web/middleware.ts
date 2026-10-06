import { NextRequest, NextResponse } from "next/server";
import {
  SESSION_COOKIE, authBlockedFor, authConfigured, authFail, authLimiters, checkBasicAuth, checkMutation, clientKey, safeNextPath, verifySessionToken,
} from "./lib/security";

// Protège TOUTES les pages et API (app perso mono-utilisateur). Identifiants : APP_USER / APP_PASSWORD (onglet Dokploy),
// APP_PASSWORD de 14 caractères minimum ; fail-closed si non configuré.
// Deux façons d'être authentifié :
//  1. cookie de session signé posé par le formulaire /login (navigateur) ;
//  2. REPLI : en-tête `Authorization: Basic …` valide (healthcheck du conteneur, scripts `curl -u`).
// Sans authentification : une navigation de page est redirigée vers /login?next=… ; une API reçoit 401 JSON (sans
// WWW-Authenticate, pour ne plus jamais ouvrir la boîte de dialogue du navigateur).
// En plus : limitation des échecs par client (429) et garde anti-CSRF sur toutes les mutations (Origin + JSON).
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|robots.txt).*)"],
};

// Chemins accessibles SANS session : la page de connexion et ses deux routes (elles font leurs propres contrôles).
const PUBLIC_PATHS = new Set(["/login", "/api/auth/login", "/api/auth/logout"]);
// Ces deux routes reçoivent un vrai formulaire HTML (application/x-www-form-urlencoded) ; Origin reste obligatoire.
const FORM_PATHS = new Set(["/api/auth/login", "/api/auth/logout"]);

// Aperçu LOCAL sans mot de passe (vérification visuelle automatique) : les TROIS conditions sont exigées — serveur de développement
// (`next dev`, jamais un build de production), DEV_NO_AUTH=1, et requête adressée à localhost / 127.0.0.1 (en-tête Host réel : en
// développement, `nextUrl.hostname` vaut toujours « localhost », quel que soit l'appelant).
function localPreview(req: NextRequest): boolean {
  const host = (req.headers.get("host") ?? "").replace(/:\d+$/, "").toLowerCase();
  return process.env.NODE_ENV === "development" && process.env.DEV_NO_AUTH === "1" && ["localhost", "127.0.0.1"].includes(host);
}

// Limiteurs partagés avec /api/auth/login (par client + plafond global tous clients confondus).
const limiters = authLimiters(Number(process.env.AUTH_MAX_FAILURES) || 10, Number(process.env.AUTH_BLOCK_MINUTES) || 15);
let warnedWeak = false;

/** Les réponses produites ici (401/403/429/redirections) ne passent pas par les en-têtes de next.config.js : on pose l'essentiel. */
function harden(res: NextResponse): NextResponse {
  res.headers.set("X-Content-Type-Options", "nosniff");
  res.headers.set("X-Frame-Options", "DENY");
  res.headers.set("Cache-Control", "no-store");
  return res;
}

/** Redirection vers un chemin INTERNE (déjà validé par safeNextPath) sur l'hôte de la requête. */
function redirectTo(req: NextRequest, path: string): NextResponse {
  const url = req.nextUrl.clone();
  const [pathname, search = ""] = path.split("?");
  url.pathname = pathname;
  url.search = search ? `?${search}` : "";
  return harden(NextResponse.redirect(url, 307));
}

function wantsPage(req: NextRequest): boolean {
  const p = req.nextUrl.pathname;
  return (req.method === "GET" || req.method === "HEAD") && !p.startsWith("/api/");
}

export async function middleware(req: NextRequest) {
  const path = req.nextUrl.pathname;
  const preview = localPreview(req);
  if (!preview) {
    const cfg = { user: process.env.APP_USER, password: process.env.APP_PASSWORD };
    if (!authConfigured(cfg) && !warnedWeak) {
      warnedWeak = true;
      console.warn("[auth] APP_USER/APP_PASSWORD absents ou APP_PASSWORD < 14 caractères : accès refusé (fail-closed).");
    }
    const session = await verifySessionToken(req.cookies.get(SESSION_COOKIE)?.value, cfg, Date.now(), process.env.APP_SESSION_SECRET);

    if (PUBLIC_PATHS.has(path)) {
      // Déjà connecté : la page de connexion renvoie directement vers la page demandée.
      if (session && path === "/login" && req.method === "GET") return redirectTo(req, safeNextPath(req.nextUrl.searchParams.get("next")));
    } else if (!session) {
      const authz = req.headers.get("authorization");
      if (authz) {
        const key = clientKey(req.headers);
        const wait = authBlockedFor(limiters, key);
        if (wait > 0) {
          return harden(new NextResponse("Trop de tentatives, réessayez plus tard", {
            status: 429, headers: { "Retry-After": String(Math.ceil(wait / 1000)) },
          }));
        }
        if (!checkBasicAuth(authz, cfg)) {
          authFail(limiters, key);
          return harden(NextResponse.json({ error: "authentification requise" }, { status: 401 }));
        }
        limiters.perClient.success(key);
      } else if (wantsPage(req)) {
        const next = safeNextPath(path + req.nextUrl.search);
        return redirectTo(req, next === "/" ? "/login" : `/login?next=${encodeURIComponent(next)}`);
      } else {
        return harden(NextResponse.json({ error: "authentification requise" }, { status: 401 }));
      }
    }
  }
  const refused = checkMutation(req.method, req.headers, process.env.APP_URL, FORM_PATHS.has(path));
  if (refused) return harden(NextResponse.json({ error: "requête refusée" }, { status: refused.status }));
  return NextResponse.next();
}
