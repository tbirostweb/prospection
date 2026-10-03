import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/db";
import { LOCAL_ACTIVITIES } from "@/lib/local";

const NAF = /^\d{2}\.\d{2}[A-Z]$/;

/** Campagnes avec leurs compteurs (prospects, sans site, à contacter). */
export async function GET() {
  const rows = await query<any>(`
    SELECT c.*, COUNT(p.id) AS prospects, COALESCE(SUM(p.website_status='NOT_FOUND'),0) AS no_site,
           COALESCE(SUM(p.category IN ('TRES_BON','A_CONTACTER')),0) AS to_contact
    FROM local_campaigns c
    LEFT JOIN local_prospect_campaigns lc ON lc.campaign_id = c.id LEFT JOIN local_prospects p ON p.id = lc.prospect_id
    GROUP BY c.id ORDER BY c.id DESC`);
  return NextResponse.json(rows);
}

/** Crée une campagne « en attente » : le worker la prend au prochain passage (toutes les 15 min). Rien n'est codé en dur. */
export async function POST(req: NextRequest) {
  const b = await req.json();
  const city = String(b.city ?? "").trim().slice(0, 128);
  const postal = String(b.postal_code ?? "").trim().slice(0, 10);
  const dep = String(b.department ?? "").trim().toUpperCase();
  if (dep && !/^(\d{2}|2A|2B|97\d)$/.test(dep)) return NextResponse.json({ error: "département invalide (ex. 10, 2A, 974)" }, { status: 400 });
  const radius = Number(b.radius_km);
  const max = Math.min(1000, Math.max(10, Number(b.max_companies) || 60));
  const known = new Set(LOCAL_ACTIVITIES.map(([k]) => k));
  const activities = (Array.isArray(b.activities) ? b.activities : []).map((a: any) => String(a).trim())
    .filter((a: string) => known.has(a) || NAF.test(a.toUpperCase())).map((a: string) => (known.has(a) ? a : a.toUpperCase()));
  // Depuis la carte : un point (latitude / longitude) remplace la ville — pas de géocodage, pas d'ambiguïté de commune.
  const lat = Number(b.latitude), lon = Number(b.longitude);
  const hasPoint = Number.isFinite(lat) && Number.isFinite(lon) && lat > 41 && lat < 51.5 && lon > -5.5 && lon < 10;
  if (!city && !postal && !dep && !hasPoint) return NextResponse.json({ error: "ville, code postal, département ou point sur la carte obligatoire" }, { status: 400 });
  if (!Number.isFinite(radius) || radius < 1 || radius > 50) return NextResponse.json({ error: "rayon entre 1 et 50 km" }, { status: 400 });
  if (!activities.length) return NextResponse.json({ error: "choisis au moins une activité (ou un code NAF)" }, { status: 400 });
  const label = city || postal || (dep && !hasPoint ? `Département ${dep}` : `point ${lat.toFixed(3)}, ${lon.toFixed(3)}`);
  const name = String(b.name ?? "").trim().slice(0, 160) || `${label} · ${radius} km`;
  const watch = b.watch ? 1 : 0;
  await query(
    `INSERT INTO local_campaigns (name, city, postal_code, department, radius_km, activities, max_companies, status, latitude, longitude, watch) VALUES (?,?,?,?,?,?,?, 'queued', ?, ?, ?)`,
    [name, label, postal || null, dep || null, radius, JSON.stringify([...new Set(activities)]), max, hasPoint ? lat : null, hasPoint ? lon : null, watch]);
  return NextResponse.json({ ok: true });
}
