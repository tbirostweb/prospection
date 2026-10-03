import { NextRequest, NextResponse } from "next/server";
import { query, queryOne } from "@/lib/db";
import { LOCAL_ACTIVITIES } from "@/lib/local";

export async function GET() {
  const rows = await query<any>("SELECT skey, value_json FROM settings");
  const out: Record<string, any> = {};
  for (const r of rows) out[r.skey] = r.value_json;
  return NextResponse.json(out);
}

// Seules ces clés de configuration sont acceptées (évite de polluer la table).
// `learned_adjustments` est calculé par le worker : lecture seule.
const ALLOWED_KEYS = ["local_home", "local_weights", "local_thresholds", "local_options", "local_presets", "local_sender", "local_banned_brands"];

const num = (v: unknown, lo: number, hi: number): number | null => {
  const n = Number(v);
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : null;
};

/** Valide et normalise une valeur ; null = refus. */
function sanitize(key: string, value: any): any | null {
  const numbers = (lo: number, hi: number) => {
    if (!value || typeof value !== "object") return null;
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(value)) {
      const n = num(v, lo, hi);
      if (n === null) return null;
      out[k] = n;
    }
    return out;
  };
  switch (key) {
    case "local_weights": return numbers(0, 100);
    case "local_thresholds": return numbers(0, 100);
    case "local_options": {
      // seuils de site (confirmé ≥ probable ≥ incertain) et poids d'activité (0 à 1,5 ; 1 = neutre) — jamais de préférence codée en dur
      if (!value || typeof value !== "object") return null;
      const w = value.website ?? {};
      const c = num(w.confirmed, 0.5, 1), pr = num(w.probable, 0.5, 1), u = num(w.uncertain, 0.3, 1);
      if (c === null || pr === null || u === null || !(u <= pr && pr <= c)) return null;
      const cw: Record<string, number> = {};
      for (const [k, v] of Object.entries(value.category_weights ?? {})) {
        const n = num(v, 0, 1.5);
        if (n === null || !/^[a-z0-9_]{1,40}$/.test(k)) return null;
        cw[k] = n;
      }
      const focus = ["redesign", "no_site", "balanced"].includes(value.focus) ? value.focus : "redesign";
      return { website: { confirmed: c, probable: pr, uncertain: u }, category_weights: cw, focus };
    }
    case "local_presets": {
      // Pré-recherches personnelles : groupes d'activités (clés du catalogue ou codes NAF). 30 au plus.
      if (!Array.isArray(value) || value.length > 30) return null;
      const known = new Set(LOCAL_ACTIVITIES.map(([k]) => k));
      const out = [];
      for (const p of value) {
        const label = String(p?.label ?? "").trim().slice(0, 60);
        const acts = (Array.isArray(p?.activities) ? p.activities : []).map((a: any) => String(a).trim())
          .filter((a: string) => known.has(a) || /^\d{2}\.\d{2}[A-Z]$/.test(a.toUpperCase())).slice(0, 40);
        if (!label || !acts.length) return null;
        out.push({ key: String(p?.key ?? "").replace(/[^a-z0-9_-]/gi, "").slice(0, 40) || `perso_${out.length}`, label, icon: String(p?.icon ?? "📌").slice(0, 4), activities: acts });
      }
      return out;
    }
    case "local_sender": {
      // Ton identité pour signer les brouillons d'e-mail (jamais envoyés automatiquement).
      if (!value || typeof value !== "object") return null;
      const f = (k: string, n: number) => String(value[k] ?? "").trim().slice(0, n);
      return { name: f("name", 80), company: f("company", 120), role: f("role", 80), phone: f("phone", 30), email: f("email", 120), website: f("website", 200), pitch: f("pitch", 400) };
    }
    case "local_banned_brands": {
      // Tes enseignes à bannir en plus de la liste intégrée (franchises, chaînes locales…) : 300 au plus, 3 caractères minimum.
      const list = Array.isArray(value) ? value : String(value ?? "").split(/\n|,/);
      const out = [...new Set(list.map((x: any) => String(x).trim().slice(0, 60)).filter((x: string) => x.length >= 3))];
      return out.length <= 300 ? out : null;
    }
    case "local_home": {
      if (!value || typeof value !== "object") return null;
      const radius = num(value.defaultRadius, 1, 50);
      if (radius === null) return null;
      return { city: String(value.city ?? "").trim().slice(0, 128), postalCode: String(value.postalCode ?? "").trim().slice(0, 10), defaultRadius: radius };
    }
    default: return null;
  }
}

export async function PUT(req: NextRequest) {
  const body = await req.json(); // { key: value_json, ... }
  const user = await queryOne<any>("SELECT id FROM users ORDER BY id LIMIT 1");
  if (!user) return NextResponse.json({ error: "no user" }, { status: 404 });
  const refused: string[] = [];
  for (const [key, value] of Object.entries(body)) {
    if (!ALLOWED_KEYS.includes(key)) continue;
    const clean = sanitize(key, value);
    if (clean === null) { refused.push(key); continue; }
    await query(`INSERT INTO settings (user_id, skey, value_json) VALUES (?,?,?)
      ON DUPLICATE KEY UPDATE value_json=VALUES(value_json)`,
      [user.id, key, JSON.stringify(clean)]);
  }
  if (refused.length) return NextResponse.json({ error: `valeurs invalides : ${refused.join(", ")}` }, { status: 400 });
  return NextResponse.json({ ok: true });
}
