import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/db";
import { readJson } from "@/lib/api";
import { IMPORT_MAX_BODY } from "@/lib/csvimport-limits";
import { ImportError, decodeUpload, previewImport } from "@/lib/csvimport-db";

export const dynamic = "force-dynamic";
const NO_STORE = { "Cache-Control": "no-store" };

/**
 * POST /api/local/import/preview — APERÇU d'un import CSV : analyse et rapprochement, AUCUNE écriture.
 * Corps JSON { filename, mime, data (base64) } ; protégé par le middleware (session + Origin/JSON anti-CSRF) comme toutes les routes.
 */
export async function POST(req: NextRequest) {
  const parsed = await readJson(req, IMPORT_MAX_BODY);
  if ("error" in parsed) return parsed.error;
  try {
    const { bytes, filename } = decodeUpload(parsed.body);
    return NextResponse.json(await previewImport(query, bytes, filename), { headers: NO_STORE });
  } catch (e: any) {
    if (e instanceof ImportError) return NextResponse.json({ error: e.message }, { status: e.status, headers: NO_STORE });
    // Code d'erreur seulement : le texte d'une erreur MySQL peut contenir des valeurs du fichier.
    console.error("[import] aperçu en échec :", e?.code ?? "erreur");
    const missing = e?.code === "ER_NO_SUCH_TABLE";
    return NextResponse.json({ error: missing ? "Journal d'import absent : la migration 026 n'est pas encore appliquée (redémarre le worker)." : "Base momentanément indisponible" },
      { status: 503, headers: NO_STORE });
  }
}
