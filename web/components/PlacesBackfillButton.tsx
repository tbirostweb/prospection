"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

/** Lancer / arrêter le passage Google Places sur les fiches « sans site » déjà trouvées. Enregistre seulement le réglage ; le worker fait le travail. */
export default function PlacesBackfillButton({ on }: { on: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    setBusy(true); setErr(null);
    const r = await fetch("/api/settings", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ local_places_backfill: { enabled: !on } }) });
    if (!r.ok) setErr((await r.json().catch(() => ({}))).error ?? "refusé");
    setBusy(false);
    router.refresh();
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button disabled={busy} onClick={go} className={on ? "btn-ghost btn-sm" : "btn-primary btn-sm no-arrow"}>
        {on ? "Arrêter" : "Interroger Google Places sur les fiches déjà trouvées"}
      </button>
      {err && <span className="text-xs text-red-700">{err}</span>}
    </span>
  );
}
