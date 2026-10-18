"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

/** Site douteux (import CSV) : c'est TOI qui tranches. Chaque décision est gardée et ajuste le seuil de confirmation des prochains sites. */
export default function SiteReview({ id, url, confidence, evidence }: { id: number; url: string; confidence: number | null; evidence: string[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function decide(action: "confirm_site" | "wrong_site") {
    if (busy) return;
    setBusy(true); setMsg(null);
    const res = await fetch(`/api/local/prospects/${id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ action }) });
    setBusy(false);
    if (!res.ok) { setMsg((await res.json().catch(() => ({}))).error ?? "Refusé"); return; }
    router.refresh();
  }

  return (
    <div className="mt-2 border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
      <p className="font-medium">Est-ce bien son site ?</p>
      <a href={url} target="_blank" rel="noopener noreferrer" className="break-all font-medium text-accent hover:underline">{url} ↗</a>
      <p className="mt-1 text-xs">Confiance calculée : {confidence != null ? `${Math.round(confidence * 100)} %` : "inconnue"}{evidence.length ? ` · ${evidence.join(" · ")}` : ""}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button disabled={busy} onClick={() => decide("confirm_site")} className="btn-primary btn-sm">C'est bien son site</button>
        <button disabled={busy} onClick={() => decide("wrong_site")} className="btn-ghost btn-sm">Ce n'est pas son site</button>
      </div>
      <p className="mt-2 text-xs">Ta réponse est enregistrée : elle aide à confirmer seuls les prochains sites aussi sûrs que celui-ci.</p>
      {msg && <p className="mt-1 text-xs text-red-700">{msg}</p>}
    </div>
  );
}
