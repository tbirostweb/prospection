"use client";
import { useState } from "react";
import { FAVORED_ACTIVITIES, LOCAL_ACTIVITIES, NOISY_ACTIVITIES, PRESETS, Preset } from "@/lib/local";

const LABEL = Object.fromEntries(LOCAL_ACTIVITIES);

/** Choix des activités : pré-recherches prêtes à l'emploi (un clic = tout le groupe), activités une par une, codes NAF libres,
 *  et « Enregistrer comme pré-recherche » pour réutiliser une sélection. */
export default function ActivityPicker({ picked, setPicked, custom, onSaved }: {
  picked: string[]; setPicked: (v: string[]) => void; custom: Preset[]; onSaved?: (all: Preset[]) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const all: Preset[] = [...PRESETS, ...custom.map((p) => ({ ...p, custom: true }))];
  const toggle = (k: string) => setPicked(picked.includes(k) ? picked.filter((x) => x !== k) : [...picked, k]);
  const isOn = (p: Preset) => p.activities.every((a) => picked.includes(a));
  const applyPreset = (p: Preset) => setPicked(isOn(p) ? picked.filter((a) => !p.activities.includes(a)) : [...new Set([...picked, ...p.activities])]);

  async function save() {
    if (!name.trim() || !picked.length) return;
    const next = [...custom, { key: `perso_${Date.now()}`, label: name.trim(), icon: "📌", activities: picked }];
    const res = await fetch("/api/settings", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ local_presets: next }) });
    if (!res.ok) { setMsg((await res.json().catch(() => ({}))).error ?? "Refusé"); return; }
    setSaving(false); setName(""); setMsg("Pré-recherche enregistrée"); onSaved?.(next);
  }

  return (
    <div className="grid gap-3">
      <div>
        <div className="mono mb-2">Pré-recherches prêtes à l'emploi</div>
        <div className="flex flex-wrap gap-2">
          {all.map((p) => (
            <button key={p.key} type="button" onClick={() => applyPreset(p)} title={p.activities.map((a) => LABEL[a] ?? a).join(", ")}
              aria-pressed={isOn(p)} className={`pill ${isOn(p) ? "!border-accent !bg-accent !text-white" : ""}`}>
              {p.icon} {p.label} <span className="opacity-60">({p.activities.length})</span>
            </button>
          ))}
        </div>
      </div>
      <div>
        <div className="mono mb-2">Ou activité par activité {picked.length ? `· ${picked.length} choisie(s)` : ""}</div>
        <p className="mb-2 text-xs text-muted">⭐ métiers favorisés (quelques clients remboursent un site) · « signal requis » : activité très concurrentielle, gardée seulement avec un vrai signal.</p>
        <div className="flex flex-wrap gap-1.5">
          {LOCAL_ACTIVITIES.map(([k, label]) => (
            <button key={k} onClick={() => toggle(k)} type="button"
              title={NOISY_ACTIVITIES.includes(k) ? "Activité très concurrentielle : seuls les établissements avec un vrai signal (pas de site, site en panne ou à moderniser, ouverture ou reprise récente) sont gardés"
                : FAVORED_ACTIVITIES.includes(k) ? "Métier favorisé : quelques nouveaux clients remboursent un site" : undefined}
              aria-pressed={picked.includes(k)} className={`pill !px-3 !text-xs md:!min-h-8 ${picked.includes(k) ? "pill-on" : ""} ${NOISY_ACTIVITIES.includes(k) && !picked.includes(k) ? "text-muted" : ""}`}>
              {FAVORED_ACTIVITIES.includes(k) ? "⭐ " : ""}{label}{NOISY_ACTIVITIES.includes(k) ? " · signal requis" : ""}</button>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {picked.length > 0 && <button type="button" onClick={() => setPicked([])} className="btn-ghost btn-sm">Tout désélectionner</button>}
        {picked.length > 0 && !saving && <button type="button" onClick={() => setSaving(true)} className="btn-ghost btn-sm">Enregistrer comme pré-recherche</button>}
        {saving && <>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Nom (ex. Coiffeurs + barbiers)" className="field w-full sm:w-auto" aria-label="Nom de la pré-recherche" />
          <button type="button" onClick={save} className="btn-primary btn-sm">Enregistrer</button>
          <button type="button" onClick={() => setSaving(false)} className="btn-ghost btn-sm">Annuler</button>
        </>}
        {msg && <span className="text-muted">{msg}</span>}
      </div>
    </div>
  );
}
