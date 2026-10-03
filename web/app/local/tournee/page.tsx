import Link from "next/link";
import { PageHeader } from "@/components/LocalUI";
import { LIST_COLUMNS, safeRows } from "@/lib/localdb";
import { LocalRow, READY_SQL, bestTime, formatPhone, siteInfo } from "@/lib/local";

export const dynamic = "force-dynamic";

const MAX_STOPS = 9;            // Google Maps accepte 9 étapes intermédiaires dans un lien d'itinéraire
const km = (a: [number, number], b: [number, number]) => {
  const r = Math.PI / 180, dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
};

/** Tournée terrain : les meilleurs prospects pas encore contactés, dans un ordre de passage court (plus proche voisin), avec l'itinéraire. */
export default async function Tournee({ searchParams }: { searchParams: Promise<{ c?: string; n?: string }> }) {
  const sp = await searchParams;
  const camps = await safeRows<any>("SELECT id, name, city, latitude, longitude, radius_km FROM local_campaigns WHERE latitude IS NOT NULL ORDER BY id DESC");
  const camp = camps.find((c) => String(c.id) === sp.c) ?? camps[0];
  const n = Math.max(3, Math.min(MAX_STOPS, Number(sp.n) || 8));
  let stops: (LocalRow & { leg: number })[] = [];
  let origin: [number, number] | null = null;
  if (camp) {
    origin = [Number(camp.latitude), Number(camp.longitude)];
    const rows = await safeRows<LocalRow>(`SELECT ${LIST_COLUMNS} FROM local_prospects p
      JOIN local_prospect_campaigns lc ON lc.prospect_id = p.id AND lc.campaign_id = ?
      WHERE p.category IN ('TRES_BON','A_CONTACTER') AND p.status IN ('DISCOVERED','ENRICHED','AUDITED','QUALIFIED','TO_CONTACT')
        AND p.do_not_contact = 0 AND p.excluded_reason IS NULL AND p.is_chain = 0 AND p.latitude IS NOT NULL AND ${READY_SQL}
      ORDER BY p.status = 'TO_CONTACT' DESC, p.prospect_score DESC LIMIT ?`, [camp.id, n * 4]);
    // Les 4n meilleurs, puis on garde les n les plus faciles à enchaîner (plus proche voisin depuis le point de départ).
    const pool = rows.map((p) => JSON.parse(JSON.stringify(p)) as LocalRow);
    let here = origin;
    while (stops.length < n && pool.length) {
      const i = pool.reduce((best, p, k) => (km(here, [Number(p.latitude), Number(p.longitude)]) < km(here, [Number(pool[best].latitude), Number(pool[best].longitude)]) ? k : best), 0);
      const [p] = pool.splice(i, 1);
      const pos: [number, number] = [Number(p.latitude), Number(p.longitude)];
      stops.push({ ...p, leg: km(here, pos) });
      here = pos;
    }
  }
  const total = stops.reduce((s, p) => s + p.leg, 0) + (origin && stops.length ? km([Number(stops[stops.length - 1].latitude), Number(stops[stops.length - 1].longitude)], origin) : 0);
  const pt = (p: LocalRow) => `${Number(p.latitude).toFixed(6)},${Number(p.longitude).toFixed(6)}`;
  const mapsUrl = origin && stops.length ? `https://www.google.com/maps/dir/?api=1&travelmode=driving&origin=${origin.join(",")}&destination=${origin.join(",")}&waypoints=${encodeURIComponent(stops.map(pt).join("|"))}` : null;

  return (
    <div className="max-w-4xl">
      <PageHeader title="Tournée" kicker="01 — Sur le terrain"
        intro="Les entreprises à contacter, dans un ordre de passage court. Passer en personne marche souvent mieux qu'un e-mail : après ta visite, marque « contacté » sur la fiche.">
        <Link href="/local/contact" className="btn-ghost btn-sm">← À contacter</Link>
      </PageHeader>
      {!camp ? <div className="card text-center text-sm text-muted">Aucune campagne géolocalisée. <Link href="/local/campagnes" className="underline">Lance une campagne</Link>.</div> : (
        <>
          <form className="mb-8 flex flex-wrap items-end gap-3">
            <label className="flex min-w-0 max-w-full flex-col gap-1.5 text-sm"><span className="mono">Départ (centre de la campagne)</span>
              <select name="c" defaultValue={String(camp.id)} className="field">{camps.map((c) => <option key={c.id} value={c.id}>{c.name} — {c.city}</option>)}</select></label>
            <label className="flex flex-col gap-1.5 text-sm"><span className="mono">Arrêts</span>
              <select name="n" defaultValue={String(n)} className="field">{[3, 4, 5, 6, 7, 8, 9].map((k) => <option key={k} value={k}>{k}</option>)}</select></label>
            <button className="btn-primary btn-sm">Calculer</button>
          </form>
          {stops.length === 0 ? <div className="card text-center text-sm text-muted">Aucune entreprise à contacter (et localisée) dans cette campagne.</div> : (
            <>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-3 border-t border-ink py-5">
                <span className="text-sm">{stops.length} arrêts · environ <b>{Math.round(total)} km</b> à vol d'oiseau, retour compris</span>
                {mapsUrl && <a href={mapsUrl} target="_blank" rel="noopener noreferrer" className="btn-primary btn-sm">Ouvrir l'itinéraire dans Google Maps</a>}
              </div>
              <ol className="border-t border-line">{stops.map((p, i) => (
                <li key={p.id} className="flex gap-4 border-b border-line py-5">
                  <span className="big-number w-12 shrink-0 pt-0.5 text-3xl text-accent">{String(i + 1).padStart(2, "0")}</span>
                  <div className="min-w-0 flex-1 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <Link href={`/local/${p.id}`} className="font-display text-lg font-bold uppercase tracking-[-0.01em] hover:text-accent">{(p.trade_name || p.company_name).replace(/\s+/g, " ")}</Link>
                      <span className="text-xs text-muted">{siteInfo(p).label}</span>
                    </div>
                    <div className="text-ink/80">{p.address ?? p.city} · {p.leg.toFixed(1).replace(".", ",")} km depuis l'arrêt précédent</div>
                    <div className="text-xs text-muted">{p.activity_label ?? "Activité non classée"} · passer {bestTime(p.activity_key)}{p.phone ? <> · <a href={`tel:${p.phone}`} className="font-medium text-accent">☎ {formatPhone(p.phone)}</a></> : null}</div>
                  </div>
                </li>))}</ol>
            </>
          )}
        </>
      )}
    </div>
  );
}
