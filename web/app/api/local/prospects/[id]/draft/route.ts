import { NextRequest, NextResponse } from "next/server";
import { query, queryOne } from "@/lib/db";
import { buildDraft, Sender } from "@/lib/draft";
import { getSetting } from "@/lib/localdb";

/** « Créer un brouillon » : construit un e-mail personnalisé à partir des faits détectés et l'enregistre (modifiable). N'ENVOIE RIEN. */
export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const p = await queryOne<any>("SELECT * FROM local_prospects WHERE id=?", [id]);
  if (!p) return NextResponse.json({ error: "prospect introuvable" }, { status: 404 });
  if (p.do_not_contact) return NextResponse.json({ error: "cette entreprise a demandé à ne plus être contactée" }, { status: 409 });
  const sender = await getSetting<Sender>("local_sender", {});
  const d = buildDraft(p, sender);
  if (!d) return NextResponse.json({ error: "aucun fait objectif ne justifie un message pour ce prospect (site sans problème détecté, ou site non vérifié) : on n'invente jamais une faiblesse" }, { status: 422 });
  await query("UPDATE local_prospects SET draft_subject=?, draft_message=?, draft_kind=?, draft_created_at=UTC_TIMESTAMP() WHERE id=?", [d.subject, d.body, d.kind.slice(0, 24), id]);
  return NextResponse.json({ ...d, senderMissing: !sender.name });
}
