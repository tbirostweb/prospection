import { NextResponse } from "next/server";
import { query } from "@/lib/db";
import { replyTables } from "@/lib/replystats";

export const dynamic = "force-dynamic";

/** Statistiques agrégées des premiers contacts effectivement réalisés. Protégées par le middleware global. */
export async function GET() {
  try {
    const rows = await query<any>(`SELECT p.activity_label, p.draft_kind, p.prospect_score, p.status, p.response_status,
        p.reply_kind, p.deal_stage, p.contact_channel
      FROM local_prospects p
      WHERE EXISTS (SELECT 1 FROM local_emails e WHERE e.prospect_id=p.id AND e.kind='first' AND e.status IN ('sent','bounced'))
        OR (p.contact_channel='formulaire' AND p.contacted_at IS NOT NULL)
        OR (p.contact_channel='telephone' AND p.contacted_at IS NOT NULL)`);
    // Pour l'analyse, conserver les groupes rares sans modifier les seuils de la page statistiques.
    const tables = replyTables(rows, 1);
    return NextResponse.json({ total: rows.length, opens: null, tables }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    console.error("[reply-stats]", error);
    return NextResponse.json({ error: "base momentanément indisponible" }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}
