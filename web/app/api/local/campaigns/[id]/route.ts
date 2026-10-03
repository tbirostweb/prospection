import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/db";

/** `run` : relance la découverte (les entreprises déjà connues ne sont jamais dupliquées) · `toggle` : pause / reprise · `watch` : veille des créations. */
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await req.json();
  const { action } = body;
  if (action === "run") {
    await query(`UPDATE local_campaigns SET status='queued', last_error=NULL, enabled=1,
                 stats = JSON_SET(COALESCE(stats, JSON_OBJECT()), '$.discovery_done', false) WHERE id=?`, [id]);
  } else if (action === "watch") {
    // Veille des nouvelles entreprises : activée / désactivée, tous les 7 ou 30 jours.
    const every = [7, 30].includes(Number(body.every_days)) ? Number(body.every_days) : 30;
    await query("UPDATE local_campaigns SET watch=?, watch_every_days=? WHERE id=?", [body.watch ? 1 : 0, every, id]);
  } else if (action === "toggle") {
    await query("UPDATE local_campaigns SET enabled = 1 - enabled WHERE id=?", [id]);
  } else return NextResponse.json({ error: "action inconnue" }, { status: 400 });
  return NextResponse.json({ ok: true });
}

/** Supprime la campagne ; les prospects déjà découverts restent (suivi, notes, « ne plus contacter » conservés). */
export async function DELETE(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  await query("DELETE FROM local_campaigns WHERE id=?", [id]);
  return NextResponse.json({ ok: true });
}
