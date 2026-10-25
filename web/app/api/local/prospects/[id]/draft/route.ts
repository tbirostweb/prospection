import { NextRequest, NextResponse } from "next/server";
import { query, queryOne } from "@/lib/db";
import { Offer, Sender, buildDraft, checkWrittenCore, wrapDraft } from "@/lib/draft";
import { getSetting } from "@/lib/localdb";
import { readJson, routeId } from "@/lib/api";

/** « Créer un brouillon » : construit un e-mail personnalisé à partir des faits détectés et l'enregistre (modifiable). N'ENVOIE RIEN.
 *  Avec `{ source: "rediger", subject, core }` : enregistre le cœur rédigé par la commande Claude Code /rediger, après les mêmes garde-fous
 *  (chiffres et liens limités aux faits du brouillon de référence) ; salutation, opposition, signature et notice sont ajoutées ici. */
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const r = await routeId(params);
  if ("error" in r) return r.error;
  const { id } = r;
  const parsed = await readJson(req);
  if ("error" in parsed) return parsed.error;
  const b = parsed.body;
  const p = await queryOne<any>("SELECT * FROM local_prospects WHERE id=?", [id]);
  if (!p) return NextResponse.json({ error: "prospect introuvable" }, { status: 404 });
  if (p.do_not_contact) return NextResponse.json({ error: "cette entreprise a demandé à ne plus être contactée" }, { status: 409 });
  const sender = await getSetting<Sender>("local_sender", {});
  const offer = await getSetting<Offer>("local_offer", {});
  const d = buildDraft(p, sender, offer);
  if (!d) return NextResponse.json({ error: "aucun fait objectif ne justifie un message pour ce prospect (site sans problème détecté, ou site non vérifié) : on n'invente jamais une faiblesse" }, { status: 422 });

  let subject = d.subject, body = d.body, kind = d.kind;
  if (b.source === "rediger") {
    const core = String(b.core ?? ""), subj = String(b.subject ?? "").trim();
    const problems = checkWrittenCore(core, subj, d);
    if (problems.length) return NextResponse.json({ error: "brouillon refusé", problems }, { status: 422 });
    subject = subj;
    body = wrapDraft(p, sender, core);
    kind = `ai_${d.kind}`;
  }
  await query("UPDATE local_prospects SET draft_subject=?, draft_message=?, draft_kind=?, draft_created_at=UTC_TIMESTAMP() WHERE id=?", [subject, body, kind.slice(0, 24), id]);
  return NextResponse.json({ subject, body, facts: d.facts, kind, senderMissing: !sender.name, noticeMissing: !sender.privacy_url,
    emailUncertain: !!p.email && p.email_kind === "UNCERTAIN" });
}
