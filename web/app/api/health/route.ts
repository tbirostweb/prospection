import { NextResponse } from "next/server";
import { query } from "@/lib/db";

export const dynamic = "force-dynamic";

/** Santé RÉELLE (base joignable) pour le healthcheck du conteneur. Protégée par l'authentification comme le reste ; aucun détail renvoyé. */
export async function GET() {
  try {
    await query("SELECT 1");
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json({ ok: false }, { status: 503 });
  }
}
