import { NextRequest, NextResponse } from "next/server";
import { transaction } from "@/lib/db";
import { readJson } from "@/lib/api";
import { IMPORT_MAX_BODY, IMPORT_MAX_ROWS } from "@/lib/csvimport-limits";
import { parseResolutions } from "@/lib/csvimport";
import { ImportError, confirmImport, decodeUpload } from "@/lib/csvimport-db";

export const dynamic = "force-dynamic";
const NO_STORE = { "Cache-Control": "no-store" };

/**
 * POST /api/local/import/confirm — applique l'import, TOUT OU RIEN (une transaction), uniquement les lignes confirmées.
 * Corps JSON { filename, mime, data (base64, le même fichier), token (jeton de l'aperçu), resolutions { ligne: {action, id?} }, skip [lignes] }.
 * Le serveur refait l'analyse : si elle diffère de l'aperçu (fiches modifiées entre-temps), rien n'est écrit (409).
 */
export async function POST(req: NextRequest) {
  const parsed = await readJson(req, IMPORT_MAX_BODY);
  if ("error" in parsed) return parsed.error;
  const b = parsed.body;
  try {
    const { bytes, filename } = decodeUpload(b);
    const skip = new Set<number>(Array.isArray(b.skip) ? b.skip.filter((x: unknown) => Number.isInteger(x)).slice(0, IMPORT_MAX_ROWS) : []);
    const summary = await transaction((q) => confirmImport(q, { bytes, filename, token: b.token, resolutions: parseResolutions(b.resolutions), skip }));
    return NextResponse.json(summary, { headers: NO_STORE });
  } catch (e: any) {
    if (e instanceof ImportError) return NextResponse.json({ error: e.message }, { status: e.status, headers: NO_STORE });
    console.error("[import] confirmation annulée :", e?.code ?? "erreur");
    const missing = e?.code === "ER_NO_SUCH_TABLE";
    return NextResponse.json({ error: missing ? "Journal d'import absent : la migration 026 n'est pas encore appliquée (redémarre le worker)."
      : "Import annulé : aucune modification n'a été enregistrée. Réessaie dans un instant." }, { status: 503, headers: NO_STORE });
  }
}
