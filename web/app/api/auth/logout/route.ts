import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, checkMutation } from "@/lib/security";

export const dynamic = "force-dynamic";

/** Déconnexion (bouton du menu, POST uniquement, origine vérifiée) : efface le cookie de session puis renvoie vers /login. */
export async function POST(req: NextRequest) {
  if (checkMutation("POST", req.headers, process.env.APP_URL, true)) {
    return NextResponse.json({ error: "requête refusée" }, { status: 403 });
  }
  const res = new NextResponse(null, { status: 303, headers: { Location: "/login", "Cache-Control": "no-store" } });
  res.cookies.set(SESSION_COOKIE, "", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
  return res;
}
