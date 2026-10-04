import { NextRequest, NextResponse } from "next/server";
import { query, queryOne } from "@/lib/db";
import { readJson } from "@/lib/api";

// Remise à zéro des entreprises trouvées. L'application ne supprime rien elle-même : elle POSE une demande que le worker exécute
// dans la minute, sous son verrou (jamais au milieu d'un passage de campagne). « Ne plus contacter », « mauvais sites »,
// retours, réglages et campagnes sont toujours conservés.
const REQUEST_KEY = "reset_request";
const RESULT_KEY = "reset_last";

const parse = (v: any) => (typeof v === "string" ? JSON.parse(v) : v);

export async function GET() {
  const [counts, req, last] = await Promise.all([
    queryOne<any>(`SELECT COUNT(*) AS prospects,
                          SUM(status IN ('CONTACTED','REPLIED','INTERESTED','WON','LOST')) AS worked,
                          (SELECT COUNT(*) FROM local_do_not_contact) AS dnc
                   FROM local_prospects`),
    queryOne<any>("SELECT value_json FROM settings WHERE skey=?", [REQUEST_KEY]),
    queryOne<any>("SELECT value_json FROM settings WHERE skey=?", [RESULT_KEY]),
  ]);
  return NextResponse.json({
    prospects: Number(counts?.prospects ?? 0), worked: Number(counts?.worked ?? 0), dnc: Number(counts?.dnc ?? 0),
    pending: req ? parse(req.value_json) : null, last: last ? parse(last.value_json) : null,
  });
}

export async function POST(req: NextRequest) {
  const parsed = await readJson(req);
  if ("error" in parsed) return parsed.error;
  const body = parsed.body;
  if (body?.confirm !== "EFFACER") return NextResponse.json({ error: "Confirmation manquante" }, { status: 400 });
  const user = await queryOne<any>("SELECT id FROM users ORDER BY id LIMIT 1");
  if (!user) return NextResponse.json({ error: "no user" }, { status: 404 });
  const value = { requested_at: new Date().toISOString(), relaunch: body?.relaunch !== false };
  await query("INSERT INTO settings (user_id, skey, value_json) VALUES (?, ?, ?) ON DUPLICATE KEY UPDATE value_json=VALUES(value_json)",
    [user.id, REQUEST_KEY, JSON.stringify(value)]);
  return NextResponse.json({ ok: true, pending: value });
}

export async function DELETE() {
  await query("DELETE FROM settings WHERE skey=?", [REQUEST_KEY]);
  return NextResponse.json({ ok: true });
}
