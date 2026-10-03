import { NextRequest, NextResponse } from "next/server";

// Protège TOUTES les pages et API par HTTP Basic Auth (app perso mono-utilisateur).
// Identifiants dans les variables d'env APP_USER / APP_PASSWORD (onglet Dokploy).
// Fail-closed : si non configuré, l'accès est refusé.
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

export function middleware(req: NextRequest) {
  if (localPreview(req)) return NextResponse.next();
  const user = process.env.APP_USER;
  const password = process.env.APP_PASSWORD;
  const auth = req.headers.get("authorization");

  if (user && password && auth === "Basic " + btoa(`${user}:${password}`)) {
    return NextResponse.next();
  }
  return new NextResponse("Authentification requise", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Prospection", charset="UTF-8"' },
  });
}
