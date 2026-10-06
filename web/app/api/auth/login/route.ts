import { NextRequest, NextResponse } from "next/server";
import {
  SESSION_COOKIE, SESSION_MAX_AGE_S, authBlockedFor, authFail, authLimiters, checkCredentials, checkMutation, clientKey, createSessionToken, safeNextPath,
} from "@/lib/security";

export const dynamic = "force-dynamic";

// Connexion par formulaire (/login) avec les identifiants APP_USER / APP_PASSWORD.
// Origin vérifiée (anti-CSRF), échecs limités par adresse IP, message d'erreur générique, rien n'est journalisé.
// Le mot de passe ne transite que dans le corps POST ; aucune redirection ne le reprend.
// Limiteurs partagés avec le middleware (par client + plafond global tous clients confondus).
const limiters = authLimiters(Number(process.env.AUTH_MAX_FAILURES) || 10, Number(process.env.AUTH_BLOCK_MINUTES) || 15);

function seeOther(path: string): NextResponse {
  const res = new NextResponse(null, { status: 303, headers: { Location: path } });
  res.headers.set("Cache-Control", "no-store");
  return res;
}

function back(error: "1" | "blocked", next: string): NextResponse {
  const q = new URLSearchParams({ error });
  if (next !== "/") q.set("next", next);
  return seeOther(`/login?${q}`);
}

export async function POST(req: NextRequest) {
  // Contrôle d'origine refait ici (en plus du middleware) : la route accepte un formulaire HTML.
  if (checkMutation("POST", req.headers, process.env.APP_URL, true)) {
    return NextResponse.json({ error: "requête refusée" }, { status: 403 });
  }
  let form: FormData;
  try { form = await req.formData(); } catch { return seeOther("/login?error=1"); }
  const next = safeNextPath(form.get("next"));
  const key = clientKey(req.headers);
  if (authBlockedFor(limiters, key) > 0) return back("blocked", next);

  const cfg = { user: process.env.APP_USER, password: process.env.APP_PASSWORD };
  if (!checkCredentials(form.get("username"), form.get("password"), cfg)) {
    authFail(limiters, key);
    return back(authBlockedFor(limiters, key) > 0 ? "blocked" : "1", next);
  }
  limiters.perClient.success(key);
  const token = await createSessionToken(cfg, Date.now(), process.env.APP_SESSION_SECRET);
  if (!token) return back("1", next);
  const res = seeOther(next);
  res.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE_S,
  });
  return res;
}
