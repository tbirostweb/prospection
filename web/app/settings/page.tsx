"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import ResetPanel from "@/components/ResetPanel";
import { LOCAL_ACTIVITIES, LOCAL_SITE_DEFAULTS, LOCAL_THRESHOLDS, LOCAL_WEIGHTS, LOCAL_WEIGHT_LABELS, RADII } from "@/lib/local";

export default function SettingsPage() {
  const [s, setS] = useState<any>(null);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [bannedText, setBannedText] = useState("");

  useEffect(() => {
    fetch("/api/settings").then((r) => r.json()).then((d) => {
      setS(d);
      setBannedText(Array.isArray(d.local_banned_brands) ? d.local_banned_brands.join("\n") : "");
    }).catch(() => {});
  }, []);

  async function save() {
    setErr(null);
    // Seules les clés de la prospection locale sont modifiables ; le reste du contenu de `settings` (ancien pipeline) n'est jamais renvoyé.
    const editable: Record<string, any> = Object.fromEntries(Object.entries(s).filter(([k]) => k.startsWith("local_")));
    editable.local_banned_brands = bannedText.split("\n").map((x) => x.trim()).filter((x) => x.length >= 3);
    const res = await fetch("/api/settings", {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(editable),
    });
    if (!res.ok) { setErr((await res.json().catch(() => ({}))).error ?? "Enregistrement refusé"); return; }
    setSaved(true); setTimeout(() => setSaved(false), 2000);
  }

  if (!s) return <p className="mono">Chargement…</p>;
  const lhome = s.local_home ?? { city: "", postalCode: "", defaultRadius: 20 };
  const lopts = { website: { ...LOCAL_SITE_DEFAULTS, ...(s.local_options?.website ?? {}) }, category_weights: (s.local_options?.category_weights ?? {}) as Record<string, number>,
    focus: (s.local_options?.focus ?? "redesign") as string };
  const lw = { ...LOCAL_WEIGHTS, ...(s.local_weights ?? {}) };
  const lth = { ...LOCAL_THRESHOLDS, ...(s.local_thresholds ?? {}) };
  const lweightTotal = Object.keys(LOCAL_WEIGHTS).reduce((a, k) => a + Number((lw as any)[k] || 0), 0);
  const setK = (key: string, sub: string, v: number) => setS({ ...s, [key]: { ...(s[key] ?? {}), [sub]: v } });
  const sender = s.local_sender ?? {};
  const setSender = (k: string, v: string) => setS({ ...s, local_sender: { ...sender, [k]: v } });
  const presets: any[] = Array.isArray(s.local_presets) ? s.local_presets : [];

  return (
    <div className="max-w-2xl">
      <header className="page-header">
        <span className="page-kicker">05 — Configuration</span>
        <h1 className="page-title">Réglages<span className="text-signal">.</span></h1>
        <p className="page-intro">Les réglages du haut suffisent dans la plupart des cas. N'oublie pas « Enregistrer » en bas.</p>
        <div className="mt-6"><Link href="/local/stats" className="btn-ghost btn-sm">Statistiques et état technique →</Link></div>
      </header>

      <section className="panel mb-12">
        <h2 className="section-title">Mon identité (signature des messages)</h2>
        <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
          {([["name", "Prénom et nom", "Théo …"], ["company", "Entreprise / marque", "…"], ["role", "Activité", "développeur web indépendant"],
             ["phone", "Téléphone", "06 …"], ["email", "E-mail", "contact@…"], ["website", "Site / portfolio", "https://…"]] as const).map(([k, label, ph]) => (
            <label key={k} className="flex flex-col gap-1"><span className="mono">{label}</span>
              <input value={sender[k] ?? ""} placeholder={ph} onChange={(e) => setSender(k, e.target.value)} className="field" /></label>
          ))}
          <label className="flex flex-col gap-1 sm:col-span-2"><span className="mono">Ta phrase de présentation (facultatif)</span>
            <textarea value={sender.pitch ?? ""} rows={2} onChange={(e) => setSender("pitch", e.target.value)} className="field"
              placeholder="Laisse vide pour une phrase adaptée automatiquement à la situation (sans site, site à moderniser…)" /></label>
        </div>
        <p className="mt-2 text-xs text-muted">Utilisé uniquement pour signer les brouillons que tu crées depuis une fiche. Rien n'est jamais envoyé par l'application.</p>
      </section>

      <section className="panel mb-12">
        <h2 className="section-title">Ma ville (valeurs par défaut des campagnes)</h2>
        <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
          <label className="flex flex-col gap-1"><span className="mono">Ville</span>
            <input value={lhome.city ?? ""} onChange={(e) => setS({ ...s, local_home: { ...lhome, city: e.target.value } })} className="field" /></label>
          <label className="flex flex-col gap-1"><span className="mono">Code postal</span>
            <input value={lhome.postalCode ?? ""} maxLength={10} onChange={(e) => setS({ ...s, local_home: { ...lhome, postalCode: e.target.value } })} className="field" /></label>
          <label className="flex flex-col gap-1"><span className="mono">Rayon par défaut</span>
            <select value={lhome.defaultRadius ?? 20} onChange={(e) => setS({ ...s, local_home: { ...lhome, defaultRadius: Number(e.target.value) } })} className="field">
              {RADII.map((r) => <option key={r} value={r}>{r} km</option>)}</select></label>
        </div>
        
      </section>

      <section className="panel mb-12">
        <h2 className="section-title">Priorité de ciblage</h2>
        <div className="flex flex-col gap-2 text-sm">
          {([["redesign", "🛠️ Refonte d'abord", "entreprises qui ONT un site daté ou faible (pas adapté au mobile, pas sécurisé, technologies anciennes, site gratuit Wix…) : le besoin se voit et se montre"],
            ["balanced", "⚖️ Équilibré", "sites à refaire et entreprises sans site à égalité"],
            ["no_site", "🆕 Sans site d'abord", "entreprises sans site trouvé (après une vraie recherche)"]] as const).map(([k, label, hint]) => (
            <label key={k} className="flex min-h-[44px] items-start gap-3 py-1"><input type="radio" name="focus" checked={lopts.focus === k} onChange={() => setS({ ...s, local_options: { ...lopts, focus: k } })} className="mt-1" />
              <span><b>{label}</b> <span className="text-muted">— {hint}</span></span></label>))}
          <p className="text-xs text-muted">Les scores sont recalculés au prochain passage du worker (ou au redémarrage).</p>
        </div>
      </section>

      <section className="panel mb-12">
        <h2 className="section-title">Enseignes bannies</h2>
        <p className="mb-3 text-sm text-muted">
          Plus de 700 chaînes et franchises sont déjà écartées automatiquement. Ajoute ici celles qui manquent, une par ligne.
        </p>
        <textarea rows={4} value={bannedText} onChange={(e) => setBannedText(e.target.value)}
          placeholder={"Pizza Mamma Mia\nLe Fournil Express"} className="field w-full font-mono text-sm" />
      </section>

      {presets.length > 0 && (
        <section className="panel mb-12">
          <h2 className="section-title">Mes pré-recherches</h2>
          <ul className="divide-y divide-line border-t border-line text-sm">
            {presets.map((p, i) => (
              <li key={p.key ?? i} className="flex items-center justify-between gap-3 py-2">
                <span>{p.icon} {p.label} <span className="text-muted">· {p.activities.length} activité(s)</span></span>
                <button onClick={() => setS({ ...s, local_presets: presets.filter((_, k) => k !== i) })} className="btn-ghost btn-sm !text-red-700">Supprimer</button>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-muted">Crée-en de nouvelles depuis « Nouvelle campagne » ou la carte : « Enregistrer comme pré-recherche ». N'oublie pas d'enregistrer.</p>
        </section>
      )}

      <details className="mb-10 border-t border-ink pt-2">
        <summary className="mono flex min-h-[44px] cursor-pointer items-center">Réglages avancés du score (à ne toucher qu'en connaissance de cause)</summary>
        <div className="mt-4">
      <section className="mb-6 border-t border-line pt-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="section-title !mb-0">Poids du score (sur 100)</h2>
          <div className="flex items-center gap-3">
            <span className={`mono ${lweightTotal === 100 ? "!text-emerald-700" : "!text-accent"}`}>Total {lweightTotal} / 100</span>
            <button onClick={() => setS({ ...s, local_weights: { ...LOCAL_WEIGHTS }, local_thresholds: { ...LOCAL_THRESHOLDS } })} className="mono min-h-[44px] hover:text-accent">Par défaut</button>
          </div>
        </div>
        <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
          {Object.keys(LOCAL_WEIGHTS).map((k) => (
            <label key={k} className="flex min-w-0 items-center justify-between gap-2">
              <span className="min-w-0 text-muted">{LOCAL_WEIGHT_LABELS[k]}</span>
              <input type="number" value={(lw as any)[k]} onChange={(e) => setK("local_weights", k, Number(e.target.value))} className="field w-20 text-right" />
            </label>
          ))}
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          {([["TRES_BON", "Très bon ≥"], ["A_CONTACTER", "À contacter ≥"], ["A_EXAMINER", "À examiner ≥"], ["FAIBLE", "Faible ≥"]] as const).map(([k, label]) => (
            <label key={k} className="flex flex-col gap-1"><span className="mono">{label}</span>
              <input type="number" value={(lth as any)[k]} onChange={(e) => setK("local_thresholds", k, Number(e.target.value))} className="field" /></label>
          ))}
        </div>
        <p className="mt-3 text-xs text-muted">Score explicable point par point, produit par des règles.</p>
      </section>

      <section className="mb-6 border-t border-line pt-4">
        <h2 className="section-title">Fiabilité du site et poids d'activité</h2>
        <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
          {([["confirmed", "Confirmé ≥"], ["probable", "Probable ≥"], ["uncertain", "Incertain ≥"]] as const).map(([k, label]) => (
            <label key={k} className="flex flex-col gap-1"><span className="mono">{label}</span>
              <input type="number" step="0.01" min={0.3} max={1} value={lopts.website[k]} onChange={(e) => setS({ ...s, local_options: { ...lopts, website: { ...lopts.website, [k]: Number(e.target.value) } } })} className="field" /></label>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted">Confiance minimale pour qualifier un site de confirmé / probable / incertain. Un site incertain n'est jamais audité. Abaisser ces seuils augmente le risque de mauvais site associé.</p>
        <div className="mono mb-2 mt-4">Poids d'activité (1 = neutre ; plus haut = plus intéressant pour toi)</div>
        <div className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
          {LOCAL_ACTIVITIES.map(([k, label]) => (
            <label key={k} className="flex min-w-0 items-center justify-between gap-2"><span className="min-w-0 truncate text-muted">{label}</span>
              <input type="number" step="0.1" min={0} max={1.5} value={lopts.category_weights[k] ?? 1} onChange={(e) => setS({ ...s, local_options: { ...lopts, category_weights: { ...lopts.category_weights, [k]: Number(e.target.value) } } })} className="field w-20 text-right" /></label>
          ))}
        </div>
      </section>

        </div>
      </details>

      <div className="flex flex-wrap items-center gap-4">
        <button onClick={save} className="btn-primary">{saved ? "✓ Enregistré" : "Enregistrer"}</button>
        {err && <span className="text-sm text-red-600">{err}</span>}
      </div>

      <div className="mt-12"><ResetPanel /></div>
    </div>
  );
}
