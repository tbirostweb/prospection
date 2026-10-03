"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Preset, RADII } from "@/lib/local";
import ActivityPicker from "./ActivityPicker";

const STATUS: Record<string, string> = { idle: "Jamais lancée", queued: "En attente du worker (≤ 15 min)", running: "En cours", done: "Terminée", error: "En erreur" };

export default function CampaignPanel({ campaigns, home, presets }: { campaigns: any[]; home: { city?: string; postalCode?: string; defaultRadius?: number }; presets: Preset[] }) {
  const router = useRouter();
  const [city, setCity] = useState(home.city ?? "");
  const [postal, setPostal] = useState(home.postalCode ?? "");
  const [dep, setDep] = useState("");
  const [radius, setRadius] = useState(home.defaultRadius ?? 20);
  const [picked, setPicked] = useState<string[]>([]);
  const [naf, setNaf] = useState("");
  const [max, setMax] = useState(60);
  const [msg, setMsg] = useState<string | null>(null);
  const [open, setOpen] = useState(campaigns.length === 0);
  const [custom, setCustom] = useState<Preset[]>(presets);
  const [watch, setWatch] = useState(false);

  async function create() {
    setMsg(null);
    const activities = [...picked, ...naf.split(/[,\s]+/).filter(Boolean)];
    const res = await fetch("/api/local/campaigns", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ city, postal_code: postal, department: dep, radius_km: radius, activities, max_companies: max, watch }) });
    if (!res.ok) { setMsg((await res.json().catch(() => ({}))).error ?? "Refusé"); return; }
    setPicked([]); setNaf(""); setOpen(false); router.refresh();
  }
  async function act(id: number, action: "run" | "toggle" | "watch", extra: any = {}) {
    await fetch(`/api/local/campaigns/${id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ action, ...extra }) });
    router.refresh();
  }
  async function del(id: number) {
    if (!confirm("Supprimer la campagne ? Les prospects déjà découverts (suivi, notes) sont conservés.")) return;
    await fetch(`/api/local/campaigns/${id}`, { method: "DELETE" });
    router.refresh();
  }

  return (
    <section className="panel mb-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="section-title !mb-0">Mes campagnes</h2>
        <button onClick={() => setOpen(!open)} className={open ? "btn-ghost btn-sm" : "btn-primary btn-sm"}>{open ? "Fermer" : "Nouvelle campagne"}</button>
      </div>
      {open && (
        <div className="mt-5 grid grid-cols-1 gap-5 border-t border-line pt-5 text-sm">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-5">
            <label className="flex flex-col gap-1 sm:col-span-2"><span className="mono">Ville</span>
              <input value={city} onChange={(e) => setCity(e.target.value)} placeholder="ex. Troyes" className="field" /></label>
            <label className="flex flex-col gap-1"><span className="mono">Code postal</span>
              <input value={postal} onChange={(e) => setPostal(e.target.value)} placeholder="10000" className="field" /></label>
            <label className="flex flex-col gap-1" title="Départage les communes homonymes ; seul, centre la recherche sur la plus grande commune du département"><span className="mono">Département</span>
              <input value={dep} onChange={(e) => setDep(e.target.value)} placeholder="10" maxLength={3} className="field" /></label>
            <label className="flex flex-col gap-1"><span className="mono">Rayon</span>
              <select value={radius} onChange={(e) => setRadius(Number(e.target.value))} className="field">
                {RADII.map((r) => <option key={r} value={r}>{r} km</option>)}</select></label>
          </div>
          <ActivityPicker picked={picked} setPicked={setPicked} custom={custom} onSaved={setCustom} />
          <details className="text-sm">
            <summary className="mono flex min-h-[44px] cursor-pointer items-center">Options avancées</summary>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
              <label className="flex flex-col gap-1 sm:col-span-2"><span className="mono">Autres codes NAF/APE (séparés par des virgules)</span>
                <input value={naf} onChange={(e) => setNaf(e.target.value)} placeholder="96.09Z, 47.78A" className="field" /></label>
              <label className="flex flex-col gap-1"><span className="mono">Entreprises maximum</span>
                <input type="number" min={10} max={1000} value={max} onChange={(e) => setMax(Number(e.target.value))} className="field" /></label>
            </div>
          </details>
          <p className="text-xs text-muted">
            Les chaînes et franchises sont écartées automatiquement. « Pas de site trouvé » veut dire qu'aucun site n'a été trouvé après recherche, pas une certitude.
          </p>
          <label className="check"><input type="checkbox" checked={watch} onChange={(e) => setWatch(e.target.checked)} />
            Prévenir (Telegram) quand une nouvelle entreprise s'installe dans cette zone</label>
          <div className="flex flex-wrap items-center gap-3">
            <button onClick={create} className="btn-primary btn-sm">Lancer la recherche</button>
            {msg && <span className="text-sm text-red-600">{msg}</span>}
          </div>
        </div>
      )}
      {campaigns.length > 0 && (
        <div className="row-list mt-5">
          {campaigns.map((c) => {
            const st = typeof c.stats === "string" ? JSON.parse(c.stats || "{}") : c.stats ?? {};
            const cov = typeof c.coverage === "string" ? JSON.parse(c.coverage || "null") : c.coverage;
                        return (
              <div key={c.id} className={`row-item flex flex-wrap items-center gap-x-6 gap-y-3 py-6 ${c.enabled ? "" : "opacity-60"}`}>
                <div className="flex min-w-0 flex-1 basis-full items-start gap-3 sm:gap-5">
                <span className="row-num shrink-0" aria-hidden />
                <div className="min-w-0 flex-1">
                  <div className="font-display text-2xl font-bold uppercase leading-[0.95] tracking-[-0.01em] md:text-[1.75rem]">{c.name}</div>
                  <div className="mono mt-1">{STATUS[c.status] ?? c.status}{!c.enabled && " · en pause"}
                    {st.remaining ? ` · ${st.remaining} restant(s)` : ""}</div>
                  {c.status === "running" && c.last_error && (/moteur|cooldown|searxng/i.test(c.last_error) ? (
                    <div className="text-sm text-muted" title={c.last_error}>
                      ⏸ Recherche de sites en pause (moteurs de recherche saturés), elle reprend toute seule{st.resume_at ? ` vers ${new Date(st.resume_at).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}` : ""}.
                    </div>
                  ) : <div className="text-sm text-amber-800">⏸ En attente : {c.last_error} (reprise automatique)</div>)}
                  {c.status !== "running" && c.last_error && <div className="text-sm text-red-700">{c.last_error}</div>}
                  {cov?.partial && (
                    <div className="mt-2 inline-block border-2 border-amber-300 bg-amber-50 px-2 py-0.5 text-xs text-amber-900">
                      Zone pas entièrement explorée : {cov.imported} entreprises importées sur {cov.units_available} ({cov.stopped === "company_limit" ? "maximum d'entreprises atteint" : cov.stopped === "page_limit" ? "limite de la source de données" : cov.stopped}{cov.radius_capped ? ", rayon limité à 50 km" : ""}).
                    </div>
                  )}
                  {cov && !cov.partial && <div className="text-xs text-muted">Zone entièrement explorée</div>}
                </div>
                </div>
                <div className="w-full">
                  <div className="stat-grid grid-cols-2 sm:grid-cols-5">
                    {([["Entreprises", c.prospects], ["Avec site", c.with_site], ["Sans site trouvé", c.no_site], ["Avec un contact", c.contacts], ["À contacter", c.to_contact]] as [string, any][]).map(([l, v], i) => (
                      <div key={l} className={`stat-block !p-3 ${i === 4 ? "stat-block-accent" : ""}`}><div className="big-number text-4xl">{Number(v ?? 0)}</div><div className="mono mt-2 !text-[10px]">{l}</div></div>))}
                  </div>
                  {Number(c.pending) > 0 && <div className="mt-2 font-mono text-xs text-muted">{c.pending} en cours de vérification (site ou contact)</div>}
                </div>
                <div className="flex w-full flex-wrap items-center gap-2 text-sm">
                  <label className="check"><input type="checkbox" checked={!!c.watch} onChange={(e) => act(c.id, "watch", { watch: e.target.checked, every_days: c.watch_every_days ?? 30 })} />
                    Prévenir des nouvelles entreprises</label>
                  {!!c.watch && <select value={c.watch_every_days ?? 30} onChange={(e) => act(c.id, "watch", { watch: true, every_days: Number(e.target.value) })} className="field text-xs" aria-label="Fréquence de la veille">
                    <option value={7}>chaque semaine</option><option value={30}>chaque mois</option></select>}
                  {!!c.watch && <span className="mono">{c.last_watch_at ? `dernière veille le ${new Date(c.last_watch_at).toLocaleDateString("fr-FR")}` : "première veille demain matin"}</span>}
                </div>
                <div className="flex flex-wrap gap-2">
                  <button onClick={() => act(c.id, "run")} className="btn-ghost btn-sm">Relancer</button>
                  <button onClick={() => act(c.id, "toggle")} className="btn-ghost btn-sm">{c.enabled ? "Pause" : "Reprendre"}</button>
                  <button onClick={() => del(c.id)} className="btn-ghost btn-sm">Supprimer</button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
