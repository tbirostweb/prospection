import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/db";
import { BOARD_SQL, CONTACT_QUEUE_SQL, LIST_COLUMNS } from "@/lib/localdb";
import { filterBoard } from "@/lib/board";
import { ExportRow, OptOut, buildExport, exportFilename } from "@/lib/export";

export const dynamic = "force-dynamic";

// Colonnes en plus de la liste (qui contient déjà l'adresse et les réseaux) : SIREN et campagnes (noms). Rien d'interne (score, preuves…) n'est écrit dans le CSV.
const EXTRA = `p.siren,
  (SELECT GROUP_CONCAT(c.name ORDER BY c.id SEPARATOR ' | ') FROM local_prospect_campaigns lc JOIN local_campaigns c ON c.id = lc.campaign_id
    WHERE lc.prospect_id = p.id) AS campaigns`;

/**
 * GET /api/local/export : CSV de la liste AFFICHÉE (protégé par le middleware comme toutes les routes).
 *   list=contact               → file « À contacter » (même requête que la page)
 *   list=all + view, q, activity, sort, contact=1, ignored=1 → page « Entreprises » avec les mêmes filtres (lib/board)
 *   email=1                    → seulement les entreprises avec un e-mail
 * Les oppositions (« ne plus contacter », liste local_do_not_contact) et exclusions ne sont JAMAIS exportées.
 */
export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const list = sp.get("list") === "contact" ? "contact" : "all";
  let rows: ExportRow[];
  let optOuts: OptOut[];
  try {
    // Fail-closed : sans la liste d'opposition, on n'exporte rien.
    [rows, optOuts] = await Promise.all([
      query<ExportRow>(`SELECT ${LIST_COLUMNS}, ${EXTRA} ${list === "contact" ? CONTACT_QUEUE_SQL : BOARD_SQL}`),
      query<OptOut>("SELECT siret, siren, email, domain FROM local_do_not_contact"),
    ]);
  } catch (e) {
    console.error("[export]", e);
    return NextResponse.json({ error: "base momentanément indisponible" }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
  if (list === "all") {
    rows = filterBoard(rows, {
      view: sp.get("view") ?? "all", q: (sp.get("q") ?? "").slice(0, 200), sort: sp.get("sort") ?? "score",
      activity: sp.get("activity") ?? "", contactOnly: sp.get("contact") === "1", showIgnored: sp.get("ignored") === "1",
    }) as ExportRow[];
  }
  const { csv, count, truncated } = buildExport(rows, optOuts, { emailOnly: sp.get("email") === "1" });
  return new NextResponse(csv, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="${exportFilename()}"`,
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "X-Export-Count": String(count),
      "X-Export-Truncated": truncated ? "1" : "0",
    },
  });
}
