"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import ResetPanel from "@/components/ResetPanel";
import { AUTO_DEFAULTS } from "@/lib/autosend";
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
  const offer = s.local_offer ?? { enabled: false, area: "", launch_price: null, normal_price: null, launch_slots_left: 0 };
  const setOffer = (k: string, v: any) => setS({ ...s, local_offer: { ...offer, [k]: v } });
  const auto = { ...AUTO_DEFAULTS, ...(s.local_auto_send ?? {}) };
  const setAuto = (k: string, v: any) => setS({ ...s, local_auto_send: { ...auto, [k]: v } });
  const numOrNull = (v: string) => (v.trim() === "" ? null : Number(v));
  const presets: any[] = Array.isArray(s.local_presets) ? s.local_presets : [];

  return (
    <div className="max-w-2xl">
      <header className="page-header flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="page-title">Réglages</h1>
          <p className="page-intro">Les réglages du haut suffisent dans la plupart des cas. N'oublie pas « Enregistrer » en bas.</p>
        </div>
        <Link href="/local/stats" className="btn-ghost btn-sm">Statistiques</Link>
      </header>

      <section className="panel">
        <h2 className="section-title">Mon identité (signature des messages)</h2>
        <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
          {([["name", "Prénom et nom", "Théo …"], ["company", "Entreprise / marque", "…"], ["role", "Activité", "développeur web indépendant"],
             ["phone", "Téléphone", "06 …"], ["email", "E-mail", "contact@…"], ["website", "Site / portfolio", "https://…"],
             ["privacy_url", "Lien de ta notice d'information (données personnelles)", "https://…/confidentialite"],
             ["logo_url", "Logo de la signature (image carrée, https)", "https://…/favicon.png"]] as const).map(([k, label, ph]) => (
            <label key={k} className="flex flex-col gap-1"><span className="mono">{label}</span>
              <input value={sender[k] ?? ""} placeholder={ph} onChange={(e) => setSender(k, e.target.value)} className="field" /></label>
          ))}
          <label className="flex flex-col gap-1 sm:col-span-2"><span className="mono">Ta phrase de présentation (facultatif)</span>
            <textarea value={sender.pitch ?? ""} rows={2} onChange={(e) => setSender("pitch", e.target.value)} className="field"
              placeholder="Laisse vide pour une phrase adaptée automatiquement à la situation (sans site, site à moderniser…)" /></label>
        </div>
        <p className="mt-2 text-xs text-muted">Signe les brouillons et, si tu l'actives plus bas, les e-mails envoyés automatiquement (logo affiché en petit à gauche de la signature).
          La notice d'information (source des données, finalité, droits, opposition) est citée dans chaque brouillon : sans elle, le bouton « Ouvrir dans ma messagerie » reste désactivé.</p>
      </section>

      <section className="panel">
        <h2 className="section-title">Envoi automatique des e-mails</h2>
        <label className="flex min-h-[44px] items-center gap-3 text-sm">
          <input type="checkbox" checked={!!auto.enabled} onChange={(e) => setAuto("enabled", e.target.checked)} />
          <span><b>Envoyer automatiquement</b> les e-mails mis en file (par /rediger ou depuis une fiche), puis les relances J+3 et J+7. Sans réponse à J+14 : abandon, plus aucun contact.</span>
        </label>
        <label className="flex min-h-[44px] items-center gap-3 text-sm">
          <input type="checkbox" checked={auto.personal_emails !== false} onChange={(e) => setAuto("personal_emails", e.target.checked)} />
          <span>Envoyer aussi aux adresses Gmail, Orange, iCloud… publiées comme contact de l'entreprise (beaucoup d'artisans n'ont que celle-là).</span>
        </label>
        <label className="flex min-h-[44px] items-center gap-3 text-sm">
          <input type="checkbox" checked={auto.warmup !== false} onChange={(e) => setAuto("warmup", e.target.checked)} />
          <span><b>Montée en charge</b> : 3 e-mails par jour la première semaine, 4 la deuxième, puis le maximum réglé ci-dessous. Protège la réputation d'une adresse neuve.</span>
        </label>
        <div className="mt-3 grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
          <label className="flex flex-col gap-1"><span className="mono">E-mails par jour (max)</span>
            <input type="number" min={1} max={30} value={auto.daily_cap} onChange={(e) => setAuto("daily_cap", Number(e.target.value) || 1)} className="field" /></label>
          <label className="flex flex-col gap-1"><span className="mono">À partir de (heure)</span>
            <input type="number" min={6} max={21} value={auto.start_hour} onChange={(e) => setAuto("start_hour", Number(e.target.value) || 10)} className="field" /></label>
          <label className="flex flex-col gap-1"><span className="mono">Jusqu'à (heure)</span>
            <input type="number" min={7} max={22} value={auto.end_hour} onChange={(e) => setAuto("end_hour", Number(e.target.value) || 16)} className="field" /></label>
          <label className="flex flex-col gap-1"><span className="mono">Pause : début (heure)</span>
            <input type="number" min={6} max={21} value={auto.pause_start} onChange={(e) => setAuto("pause_start", Number(e.target.value) || 12)} className="field" /></label>
          <label className="flex flex-col gap-1"><span className="mono">Pause : fin (heure)</span>
            <input type="number" min={7} max={22} value={auto.pause_end} onChange={(e) => setAuto("pause_end", Number(e.target.value) || 13)} className="field" /></label>
          <label className="flex flex-col gap-1"><span className="mono">Minutes entre deux e-mails (min)</span>
            <input type="number" min={15} max={240} value={auto.min_gap_min} onChange={(e) => setAuto("min_gap_min", Number(e.target.value) || 60)} className="field" /></label>
        </div>
        <p className="mt-2 text-xs text-muted">Jours ouvrés seulement, hors jours fériés, un seul e-mail à la fois, espacé d'au moins l'intervalle ci-dessus (+ 0 à 10 min au hasard), relances comprises. Par défaut 10 h-16 h, pause 12 h-13 h, 1 e-mail par heure : 5 par jour au plus. La pause est ignorée si elle sort de la plage d'envoi. Jamais par formulaire ni Facebook. Une réponse arrête aussitôt les relances (alerte Telegram) ; un refus ajoute l'entreprise à « ne plus contacter ».
          Rien ne part tant que la boîte d'envoi et le DNS ne sont pas prêts : voir <Link href="/local/envois" className="underline">Envois</Link>.</p>
      </section>

      <section className="panel">
        <h2 className="section-title">Mon offre (dans les brouillons)</h2>
        <label className="flex min-h-[44px] items-center gap-3 text-sm">
          <input type="checkbox" checked={!!offer.enabled} onChange={(e) => setOffer("enabled", e.target.checked)} />
          <span>Présenter mon offre « clé en main » dans les brouillons (textes écrits pour le client, mise en ligne en 2 à 4 semaines, fiche Google, bilan à 3 mois, photos en option pour les métiers concernés et proches, lien vers tes réalisations)</span>
        </label>
        <div className="mt-3 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
          <label className="flex flex-col gap-1 sm:col-span-2"><span className="mono">Où tu es (après « Je suis … »)</span>
            <input value={offer.area ?? ""} placeholder="basé près de Troyes" onChange={(e) => setOffer("area", e.target.value)} className="field" /></label>
          <label className="flex flex-col gap-1"><span className="mono">Prix normal du site (€)</span>
            <input type="number" min={0} value={offer.normal_price ?? ""} onChange={(e) => setOffer("normal_price", numOrNull(e.target.value))} className="field" /></label>
          <label className="flex flex-col gap-1"><span className="mono">Prix de lancement (€)</span>
            <input type="number" min={0} value={offer.launch_price ?? ""} onChange={(e) => setOffer("launch_price", numOrNull(e.target.value))} className="field" /></label>
          <label className="flex flex-col gap-1"><span className="mono">Photos proposées jusqu'à (km)</span>
            <input type="number" min={0} max={100} value={offer.photos_max_km ?? 20} onChange={(e) => setOffer("photos_max_km", Number(e.target.value) || 0)} className="field" /></label>
          <label className="flex flex-col gap-1"><span className="mono">Places de lancement restantes</span>
            <input type="number" min={0} max={20} value={offer.launch_slots_left ?? 0} onChange={(e) => setOffer("launch_slots_left", Number(e.target.value) || 0)} className="field" /></label>
          <label className="flex flex-col gap-1 sm:col-span-2"><span className="mono">Lien vers mes offres</span>
            <input type="url" value={offer.offers_url ?? ""} placeholder="https://…" onChange={(e) => setOffer("offers_url", e.target.value)} className="field" /></label>
        </div>
        <p className="mt-2 text-xs text-muted">Entreprises qui ont déjà un site (refonte) : aucun prix dans le message, car il dépend de l'état du site. Le message renvoie vers ce lien (http ou https) : « Mes offres et tarifs sont détaillés sur … ». Vide = le site de ta signature ; sans site, pas de phrase d'offre.</p>
        <p className="mt-2 text-xs text-muted">Places restantes au-dessus de 0 : le brouillon peut présenter ton offre de lancement (prix de lancement au lieu du prix normal). Jamais de réduction contre un avis Google : c'est interdit par Google.
          Passe-les à 0 dès que tes premiers clients ont signé. Sans prix, aucune phrase de prix n'est écrite.</p>
      </section>

      <section className="panel">
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

      <section className="panel">
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

      <section className="panel">
        <h2 className="section-title">Enseignes bannies</h2>
        <p className="mb-3 text-sm text-muted">
          Plus de 700 chaînes et franchises sont déjà écartées automatiquement. Ajoute ici celles qui manquent, une par ligne.
        </p>
        <textarea rows={4} value={bannedText} onChange={(e) => setBannedText(e.target.value)}
          placeholder={"Pizza Mamma Mia\nLe Fournil Express"} className="field w-full font-mono text-sm" />
      </section>

      {presets.length > 0 && (
        <section className="panel">
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

      <details className="panel">
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
