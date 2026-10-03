"use client";
import { loadLeaflet } from "@/lib/leaflet";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  CATEGORY_COLORS, CATEGORY_LABELS, LocalRow, PIPELINE_STAGES, Preset, RADII, SITE_COLORS, WEBSITE_LABELS, pipelineStage,
} from "@/lib/local";
import ActivityPicker from "./ActivityPicker";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type LMap = any;
let L: any = null;

type ColorMode = "score" | "stage" | "category" | "site";
interface Camp { id: number; name: string; latitude: number | string | null; longitude: number | string | null; radius_km: number | string }
const STORE = "prospection.carte.v1";
const FRANCE: [number, number] = [46.6, 2.4];

function km(a: [number, number], b: [number, number]): number {
  const R = 6371, dLat = ((b[0] - a[0]) * Math.PI) / 180, dLon = ((b[1] - a[1]) * Math.PI) / 180;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos((a[0] * Math.PI) / 180) * Math.cos((b[0] * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}
const esc = (s: any) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
const stageOf = (p: LocalRow) => pipelineStage(p as any);
const STAGE_COLOR = Object.fromEntries(PIPELINE_STAGES.map((s) => [s.key, s.color]));
const STAGE_LABEL = Object.fromEntries(PIPELINE_STAGES.map((s) => [s.key, s.label]));

// Intérêt : du rouge (pas intéressant) au vert (très intéressant) d'après la note ; gris = pas encore noté, noir = écarté.
function scoreColor(p: LocalRow): string {
  if (p.do_not_contact || p.excluded_reason || p.is_chain || p.category === "IGNORER") return "#374151";
  if (p.prospect_score == null) return "#d1d5db";
  const t = Math.max(0, Math.min(1, (Number(p.prospect_score) - 25) / (80 - 25)));
  return `hsl(${Math.round(t * 120)}, 78%, ${t > 0.4 && t < 0.7 ? 44 : 46}%)`;
}

function colorOf(p: LocalRow, mode: ColorMode): string {
  if (mode === "score") return scoreColor(p);
  if (mode === "category") return CATEGORY_COLORS[p.category ?? ""] ?? "#fff";
  if (mode === "site") return SITE_COLORS[p.website_status ?? ""] ?? "#9ca3af";
  return STAGE_COLOR[stageOf(p)];
}

export default function MapView({ rows, campaigns, presets, defaultRadius }: { rows: LocalRow[]; campaigns: Camp[]; presets: Preset[]; defaultRadius: number }) {
  const router = useRouter();
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<LMap | null>(null);
  const layer = useRef<any>(null);
  const campLayer = useRef<any>(null);
  const circle = useRef<any>(null);
  const pin = useRef<any>(null);
  const markers = useRef<Map<number, any>>(new Map());

  const [items, setItems] = useState(rows);
  const pts = useMemo(() => items.filter((p) => p.latitude != null && p.longitude != null), [items]);
  const initial = (): { center: [number, number]; radius: number } => {
    try { const v = JSON.parse(localStorage.getItem(STORE) || "null"); if (v?.center && v?.radius) return v; } catch { /* stockage indisponible */ }
    if (pts.length) {                                      // là où sont tes prospects : médiane des positions (insensible aux points isolés)
      const med = (xs: number[]) => xs.sort((a, b) => a - b)[Math.floor(xs.length / 2)];
      return { center: [med(pts.map((p) => Number(p.latitude))), med(pts.map((p) => Number(p.longitude)))], radius: defaultRadius };
    }
    const c = campaigns.find((x) => x.latitude != null);
    if (c) return { center: [Number(c.latitude), Number(c.longitude)], radius: Number(c.radius_km) || defaultRadius };
    return { center: FRANCE, radius: defaultRadius };
  };
  const [center, setCenter] = useState<[number, number]>(FRANCE);
  const [radius, setRadius] = useState<number>(defaultRadius);
  const [ready, setReady] = useState(false);
  const [mode, setMode] = useState<ColorMode>("score");
  const [stages, setStages] = useState<string[]>([]);
  const [cats, setCats] = useState<string[]>([]);
  const [site, setSite] = useState("");
  const [activity, setActivity] = useState("");
  const [minScore, setMinScore] = useState(0);
  const [withContact, setWithContact] = useState(false);
  const [onlyNew, setOnlyNew] = useState(false);
  const [insideOnly, setInsideOnly] = useState(true);
  const [showCamps, setShowCamps] = useState(true);
  const [city, setCity] = useState("");
  const [panel, setPanel] = useState<"list" | "campaign">("list");
  const [picked, setPicked] = useState<string[]>([]);
  const [custom, setCustom] = useState<Preset[]>(presets);
  const [max, setMax] = useState(200);
  const [watch, setWatch] = useState(true);
  const [msg, setMsg] = useState<string | null>(null);

  const [loadErr, setLoadErr] = useState<string | null>(null);
  useEffect(() => {
    const v = initial(); setCenter(v.center); setRadius(v.radius);
    loadLeaflet().then((lib) => { L = lib; setReady(true); }).catch((e) => setLoadErr(e.message));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (ready) try { localStorage.setItem(STORE, JSON.stringify({ center, radius })); } catch { /* stockage indisponible */ } }, [center, radius, ready]);

  const activities = useMemo(() => [...new Set(pts.map((p) => p.activity_label).filter(Boolean) as string[])].sort(), [pts]);
  const filtered = useMemo(() => pts.filter((p) => {
    const st = stageOf(p);
    if (stages.length && !stages.includes(st)) return false;
    if (cats.length && !cats.includes(p.category ?? "")) return false;
    if (site && (p.website_status ?? "NONE") !== site) return false;
    if (activity && p.activity_label !== activity) return false;
    if (minScore && (p.prospect_score ?? 0) < minScore) return false;
    if (withContact && !(p.phone || p.email || p.contact_form)) return false;
    if (onlyNew && !p.new_business_at) return false;
    return true;
  }), [pts, stages, cats, site, activity, minScore, withContact, onlyNew]);
  const inside = useMemo(() => filtered.map((p) => ({ p, d: km(center, [Number(p.latitude), Number(p.longitude)]) })).filter((x) => x.d <= radius)
    .sort((a, b) => (b.p.prospect_score ?? -1) - (a.p.prospect_score ?? -1)), [filtered, center, radius]);
  const insideIds = useMemo(() => new Set(inside.map((x) => x.p.id)), [inside]);
  const counts = useMemo(() => Object.fromEntries(PIPELINE_STAGES.map((s) => [s.key, inside.filter((x) => stageOf(x.p) === s.key).length])), [inside]);

  const move = useCallback(async (id: number, status: string) => {
    const res = await fetch(`/api/local/prospects/${id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ status }) });
    if (!res.ok) { setMsg((await res.json().catch(() => ({}))).error ?? "Refusé"); return; }
    setItems((xs) => xs.map((p) => (p.id === id ? { ...p, status } : p)));
    map.current?.closePopup();
    setMsg(null);
  }, []);

  // carte : créée une fois
  useEffect(() => {
    if (!ready || !mapEl.current || map.current) return;
    const m = L.map(mapEl.current, { preferCanvas: true, zoomControl: true }).setView(center, radius > 20 ? 9 : radius > 8 ? 11 : 12);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; contributeurs OpenStreetMap" }).addTo(m);
    campLayer.current = L.layerGroup().addTo(m);
    layer.current = L.layerGroup().addTo(m);
    circle.current = L.circle(center, { radius: radius * 1000, color: "#F0451E", weight: 2, fillOpacity: 0.04 }).addTo(m);
    pin.current = L.marker(center, { draggable: true, title: "Centre de la zone (déplaçable)",
      icon: L.divIcon({ className: "", html: '<div style="width:18px;height:18px;background:#F0451E;border:3px solid #231F20"></div>', iconSize: [18, 18], iconAnchor: [9, 9] }) }).addTo(m);
    pin.current.on("dragend", () => { const ll = pin.current!.getLatLng(); setCenter([ll.lat, ll.lng]); });
    m.on("click", (e: any) => setCenter([e.latlng.lat, e.latlng.lng]));
    m.on("popupopen", (e: any) => {
      e.popup.getElement()?.querySelectorAll("button[data-status]").forEach((b: HTMLButtonElement) =>
        b.addEventListener("click", () => move(Number(b.dataset.id), b.dataset.status!)));
    });
    map.current = m;
    return () => { m.remove(); map.current = null; };
  }, [ready]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { circle.current?.setLatLng(center).setRadius(radius * 1000); pin.current?.setLatLng(center); }, [center, radius]);

  useEffect(() => {
    if (!ready || !L || !campLayer.current) return;
    campLayer.current.clearLayers();
    if (!showCamps) return;
    for (const c of campaigns) {
      if (c.latitude == null) continue;
      L.circle([Number(c.latitude), Number(c.longitude)], { radius: Number(c.radius_km) * 1000, color: "#6b7280", weight: 1, dashArray: "4 4", fill: false, interactive: false })
        .bindTooltip(c.name).addTo(campLayer.current!);
    }
  }, [campaigns, showCamps, ready]);

  useEffect(() => {
    const g = layer.current;
    if (!ready || !L || !g) return;
    g.clearLayers();
    markers.current.clear();
    for (const p of filtered) {
      const inZone = insideIds.has(p.id);
      if (insideOnly && !inZone) continue;
      const st = stageOf(p);
      const big = p.category === "TRES_BON" ? 9 : p.category === "A_CONTACTER" ? 7.5 : 6;
      const mk = L.circleMarker([Number(p.latitude), Number(p.longitude)], {
        radius: big, color: p.new_business_at ? "#F0451E" : "#231F20", weight: p.new_business_at ? 2.5 : 1,
        fillColor: colorOf(p, mode), fillOpacity: inZone ? 0.95 : 0.35,
      });
      const phone = p.phone ? `<a href="tel:${esc(p.phone)}">☎ ${esc(p.phone.replace(/(\d{2})(?=\d)/g, "$1 "))}</a>` : "";
      const mail = p.email ? `<a href="mailto:${esc(p.email)}">✉ ${esc(p.email)}</a>` : "";
      const btn = (s: string, l: string) => (p.status === s ? "" : `<button data-id="${p.id}" data-status="${s}" style="border:2px solid #231F20;border-radius:0;padding:2px 6px;margin:2px 2px 0 0;font-size:12px;background:#fff;cursor:pointer">${l}</button>`);
      const actions = p.do_not_contact ? "<div style='color:#b91c1c'>Ne plus contacter</div>"
        : btn("TO_CONTACT", "⭐ À contacter") + btn("CONTACTED", "📞 Contacté") + btn("REPLIED", "💬 Réponse") + btn("INTERESTED", "🤝 Intéressé") + btn("WON", "🏆 Gagné") + btn("LOST", "🚫 Perdu");
      mk.bindPopup(`<div style="min-width:200px;max-width:260px;font-size:13px;line-height:1.35">
        <div style="font-weight:600;font-size:14px">${esc(p.trade_name || p.company_name)}</div>
        <div style="color:#6b7280">${esc([p.activity_label, p.city].filter(Boolean).join(" · "))}</div>
        <div style="margin-top:4px"><b>${p.prospect_score ?? "–"}/100</b>${p.category ? " · " + esc(CATEGORY_LABELS[p.category]) : ""}</div>
        <div>${esc(p.website_status ? WEBSITE_LABELS[p.website_status] : "site pas encore recherché")}${p.website_url ? ` · <a href="${esc(p.website_url)}" target="_blank" rel="noopener">site ↗</a>` : ""}</div>
        <div><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${STAGE_COLOR[st]};margin-right:4px"></span>${esc(STAGE_LABEL[st])}${p.new_business_at ? " · 🆕 nouvelle entreprise" : ""}</div>
        ${phone || mail ? `<div style="margin-top:4px">${[phone, mail].filter(Boolean).join("<br>")}</div>` : "<div style='color:#6b7280;margin-top:4px'>aucune coordonnée trouvée</div>"}
        <div style="margin-top:6px">${actions}</div>
        <div style="margin-top:6px"><a href="/local/${p.id}">Ouvrir la fiche →</a></div></div>`, { autoPanPadding: [20, 20], maxWidth: 280 });
      mk.addTo(g);
      markers.current.set(p.id, mk);
    }
  }, [filtered, insideIds, insideOnly, mode, ready]);

  function focus(id: number) {
    const mk = markers.current.get(id);
    if (mk && map.current) { map.current.setView(mk.getLatLng(), Math.max(map.current.getZoom(), 15)); mk.openPopup(); }
  }
  async function goCity() {
    if (!city.trim()) return;
    try {
      const r = await fetch(`https://geo.api.gouv.fr/communes?nom=${encodeURIComponent(city.trim())}&fields=nom,centre,codesPostaux&boost=population&limit=1`);
      const d = await r.json();
      const c = d?.[0]?.centre?.coordinates;
      if (!c) { setMsg("Commune introuvable"); return; }
      setCenter([c[1], c[0]]); map.current?.setView([c[1], c[0]], radius > 20 ? 9 : 11); setMsg(null);
    } catch { setMsg("Recherche de commune indisponible"); }
  }
  async function createCampaign() {
    if (!picked.length) { setMsg("Choisis au moins une activité ou une pré-recherche"); return; }
    const res = await fetch("/api/local/campaigns", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ latitude: center[0], longitude: center[1], radius_km: radius, activities: picked, max_companies: max, watch, city: city.trim() || undefined }) });
    if (!res.ok) { setMsg((await res.json().catch(() => ({}))).error ?? "Refusé"); return; }
    setMsg("Campagne créée : le worker la traite au prochain passage (≤ 15 min)."); setPicked([]); setPanel("list"); router.refresh();
  }
  const toggle = (arr: string[], set: (v: string[]) => void, v: string) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const legend = mode === "stage" ? PIPELINE_STAGES.map((s) => [s.label, s.color])
    : mode === "category" ? Object.entries(CATEGORY_LABELS).map(([k, l]) => [l, CATEGORY_COLORS[k]])
    : Object.entries(WEBSITE_LABELS).map(([k, l]) => [l, SITE_COLORS[k]]);

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
      <div className="min-w-0">
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
          <input value={city} onChange={(e) => setCity(e.target.value)} onKeyDown={(e) => e.key === "Enter" && goCity()} placeholder="Aller à une ville…" className="field min-w-0 flex-1 sm:flex-none" aria-label="Ville" />
          <button onClick={goCity} className="btn-ghost btn-sm">Aller</button>
          <label className="flex min-h-[44px] items-center gap-2">Rayon <input type="range" className="min-w-0 flex-1 sm:flex-none" min={1} max={50} value={radius} onChange={(e) => setRadius(Number(e.target.value))} aria-label="Rayon" />
            <span className="w-12 font-mono text-xs">{radius} km</span></label>
          <select value={radius} onChange={(e) => setRadius(Number(e.target.value))} className="field text-xs" aria-label="Rayon rapide">{RADII.map((r) => <option key={r} value={r}>{r} km</option>)}</select>
          <select value={mode} onChange={(e) => setMode(e.target.value as ColorMode)} className="field max-w-full text-xs" aria-label="Couleur des pastilles">
            <option value="score">Couleur : intérêt (vert → rouge)</option><option value="stage">Couleur : où j'en suis</option><option value="category">Couleur : catégorie</option><option value="site">Couleur : état du site</option></select>
        </div>
        {loadErr && <p className="mb-2 rounded-lg border border-red-200 bg-red-50 p-2 text-sm text-red-800">{loadErr}</p>}
        <div ref={mapEl} className="h-[62vh] min-h-[380px] w-full border-[3px] border-ink" style={{ zIndex: 0 }} />
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {mode === "score" ? (
            <span className="flex flex-wrap items-center gap-2">Pas intéressant
              <span className="inline-block h-2.5 w-32 rounded-full" style={{ background: "linear-gradient(90deg, hsl(0,78%,46%), hsl(60,78%,44%), hsl(120,78%,46%))" }} />
              Très intéressant
              <span className="ml-2 flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full border border-gray-700" style={{ background: "#d1d5db" }} />pas encore noté</span>
              <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full border border-gray-700" style={{ background: "#374151" }} />écarté</span>
            </span>
          ) : legend.map(([l, c]) => <span key={l} className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full border border-gray-700" style={{ background: c }} />{l}</span>)}
          <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full border-2" style={{ borderColor: "#F0451E" }} />🆕 nouvelle entreprise</span>
          <span className="text-muted">Clique sur la carte ou déplace le carré orange pour changer de centre.</span>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <span className="mono">Où j'en suis :</span>
          {PIPELINE_STAGES.map((s) => (
            <button key={s.key} onClick={() => toggle(stages, setStages, s.key)}
              aria-pressed={stages.includes(s.key)} className={`pill !px-3 !text-xs md:!min-h-8 ${stages.includes(s.key) ? "pill-on" : ""}`}>
              <span className="inline-block h-2 w-2 rounded-full" style={{ background: s.color }} />{s.label} <span className="opacity-60">{counts[s.key]}</span></button>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <span className="mono">Catégorie :</span>
          {Object.entries(CATEGORY_LABELS).map(([k, l]) => (
            <button key={k} onClick={() => toggle(cats, setCats, k)} aria-pressed={cats.includes(k)} className={`pill !px-3 !text-xs md:!min-h-8 ${cats.includes(k) ? "pill-on" : ""}`}>{l}</button>))}
          <select value={site} onChange={(e) => setSite(e.target.value)} className="field text-xs" aria-label="État du site"><option value="">Tous les sites</option>
            {Object.entries(WEBSITE_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}<option value="NONE">Pas encore recherché</option></select>
          <select value={activity} onChange={(e) => setActivity(e.target.value)} className="field max-w-full text-xs" aria-label="Activité"><option value="">Toutes activités</option>
            {activities.map((a) => <option key={a}>{a}</option>)}</select>
          <label className="flex items-center gap-1">score ≥ <input type="number" min={0} max={100} value={minScore || ""} onChange={(e) => setMinScore(Number(e.target.value))} className="field w-16" /></label>
          <label className="check !gap-1.5"><input type="checkbox" checked={withContact} onChange={(e) => setWithContact(e.target.checked)} />contact dispo</label>
          <label className="check !gap-1.5"><input type="checkbox" checked={onlyNew} onChange={(e) => setOnlyNew(e.target.checked)} />🆕 nouvelles</label>
          <label className="check !gap-1.5"><input type="checkbox" checked={insideOnly} onChange={(e) => setInsideOnly(e.target.checked)} />seulement dans le cercle</label>
          <label className="check !gap-1.5"><input type="checkbox" checked={showCamps} onChange={(e) => setShowCamps(e.target.checked)} />zones des campagnes</label>
        </div>
        {msg && <p className="mt-2 text-sm text-amber-900">{msg}</p>}
      </div>

      <aside className="min-w-0 border-t border-ink pt-4 xl:border-l xl:border-t-0 xl:pl-5 xl:pt-0">
        <div className="mb-3 flex flex-wrap gap-2">
          <button onClick={() => setPanel("list")} className={`btn-sm no-arrow ${panel === "list" ? "btn-primary" : "btn-ghost"}`}>Dans la zone ({inside.length})</button>
          <button onClick={() => setPanel("campaign")} className={`btn-sm no-arrow ${panel === "campaign" ? "btn-primary" : "btn-ghost"}`}>Lancer une campagne ici</button>
        </div>
        {panel === "list" ? (
          <div className="flex max-h-[70vh] flex-col overflow-y-auto border-t border-line xl:max-h-[62vh]">
            {inside.slice(0, 300).map(({ p, d }) => (
              <button key={p.id} onClick={() => focus(p.id)} className="min-h-[44px] border-b border-line py-2.5 text-left text-sm hover:bg-paper/50">
                <div className="flex items-center gap-2"><span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: STAGE_COLOR[stageOf(p)] }} />
                  <span className="truncate font-medium">{p.trade_name || p.company_name}</span><span className="ml-auto font-mono text-xs">{p.prospect_score ?? "–"}</span></div>
                <div className="mono !text-[10px]">{[p.activity_label, `${d.toFixed(1).replace(".", ",")} km`, p.website_status ? WEBSITE_LABELS[p.website_status] : null].filter(Boolean).join(" · ")}{p.new_business_at ? " · 🆕" : ""}</div>
              </button>
            ))}
            {inside.length === 0 && <p className="text-sm text-muted">Aucun prospect dans ce cercle avec ces filtres. Élargis le rayon, ou lance une campagne ici.</p>}
            {inside.length > 300 && <p className="mono">300 premiers affichés (par score).</p>}
            <Link href="/local" className="mono mt-2 inline-flex min-h-[44px] items-center hover:text-accent">Voir la liste complète →</Link>
          </div>
        ) : (
          <div className="grid gap-3 text-sm">
            <p className="text-xs text-muted">Zone : le cercle orange ({radius} km autour du point choisi). Le worker découvre les entreprises de ces activités, cherche leur site et leurs contacts.</p>
            <ActivityPicker picked={picked} setPicked={setPicked} custom={custom} onSaved={setCustom} />
            <label className="flex items-center gap-2">Entreprises max <input type="number" min={10} max={1000} value={max} onChange={(e) => setMax(Number(e.target.value))} className="field w-24" /></label>
            <label className="check"><input type="checkbox" checked={watch} onChange={(e) => setWatch(e.target.checked)} />🆕 Surveiller ensuite les nouvelles entreprises (tous les 30 jours)</label>
            <button onClick={createCampaign} className="btn-primary btn-sm">Lancer la campagne</button>
          </div>
        )}
      </aside>
    </div>
  );
}
