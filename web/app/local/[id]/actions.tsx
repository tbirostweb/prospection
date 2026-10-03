"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { DISLIKE_REASONS, STATUS_LABELS } from "@/lib/local";

// Les étapes du suivi, dans l'ordre. « Ne plus contacter » est à part (définitif).
const STEPS: { status: string; label: string; response?: string }[] = [
  { status: "TO_CONTACT", label: "À contacter" },
  { status: "CONTACTED", label: "Contacté" },
  { status: "REPLIED", label: "Réponse reçue", response: "REPLIED" },
  { status: "INTERESTED", label: "Intéressé" },
  { status: "WON", label: "Client obtenu", response: "WON" },
  { status: "LOST", label: "Pas intéressant", response: "NOT_A_FIT" },
];

/** Suivi commercial : tout est fait PAR TOI. Aucun envoi automatique. « Ne plus contacter » est définitif depuis l'interface. */
export default function ProspectActions({ id, status, notes, doNotContact, websiteUrl, siteAccepted, feedbackReason }: { id: number; status: string; notes: string | null; doNotContact: boolean; websiteUrl: string | null; siteAccepted: boolean; feedbackReason: string | null }) {
  const router = useRouter();
  const [text, setText] = useState(notes ?? "");
  const [msg, setMsg] = useState<string | null>(null);
  const [reason, setReason] = useState(feedbackReason ?? "");
  const [foundUrl, setFoundUrl] = useState("");
  const [showFound, setShowFound] = useState(false);

  async function patch(body: any) {
    setMsg(null);
    const res = await fetch(`/api/local/prospects/${id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok) { setMsg((await res.json().catch(() => ({}))).error ?? "Refusé"); return; }
    setMsg("Enregistré"); router.refresh();
  }
  async function wrongSite() {
    if (!confirm("Ce site n'est pas celui de l'entreprise ? Il sera retiré (et jamais reproposé pour elle) et la recherche relancée.")) return;
    await patch({ action: "wrong_site" });
  }
  async function foundSite() {
    if (!foundUrl.trim()) return;
    await patch({ action: "found_site", url: foundUrl.trim() });
    setShowFound(false); setFoundUrl("");
  }
  async function dnc() {
    if (!confirm("Marquer « ne plus contacter » ? L'entreprise sera exclue de toute future campagne. Ce choix ne se défait pas depuis l'interface.")) return;
    await patch({ status: "DO_NOT_CONTACT", reason: "demande de ne plus être contactée (saisie manuelle)" });
  }
  const current = status === "QUALIFIED" ? "TO_CONTACT" : status;

  return (
    <section className="panel mb-12">
      <h2 className="section-title">Où j'en suis</h2>
      {doNotContact ? (
        <p className="mb-3 text-sm font-medium text-red-700">Cette entreprise a demandé à ne plus être contactée : exclue de toute campagne.</p>
      ) : (
        <>
          <div className="mb-5 flex flex-wrap gap-2">
            {STEPS.map((a) => (
              <button key={a.status} onClick={() => patch({ status: a.status, ...(a.response ? { response_status: a.response } : {}), ...(a.status === "LOST" && reason ? { feedback_reason: reason } : {}) })}
                aria-pressed={current === a.status} className={`pill ${current === a.status ? "pill-on" : ""}`}>
                {a.label}
              </button>
            ))}
          </div>
          {!STEPS.some((a) => a.status === current) && <p className="mb-3 text-xs text-muted">Statut actuel : {STATUS_LABELS[status] ?? status}</p>}
          {current === "LOST" && (
            <label className="mb-3 flex items-center gap-2 text-sm"><span className="text-muted">Pourquoi ?</span>
              <select value={reason} onChange={(e) => { setReason(e.target.value); if (e.target.value) patch({ feedback_reason: e.target.value }); }} className="field">
                <option value="">— facultatif —</option>{DISLIKE_REASONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          )}
        </>
      )}
      <label className="flex flex-col gap-1.5 text-sm"><span className="mono">Notes</span>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} className="field w-full" placeholder="Appel du 21/09, à rappeler jeudi…" /></label>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button onClick={() => patch({ notes: text })} className="btn-primary btn-sm">Enregistrer la note</button>
        {msg && <span className="text-sm text-muted">{msg}</span>}
      </div>
      {!doNotContact && (
        <div className="mt-6 flex flex-wrap gap-x-5 border-t border-line pt-2 text-sm">
          {websiteUrl && <button onClick={wrongSite} className="min-h-[44px] text-muted underline underline-offset-2 hover:text-ink">Ce n'est pas son site</button>}
          {!siteAccepted && <button onClick={() => setShowFound(!showFound)} className="min-h-[44px] text-muted underline underline-offset-2 hover:text-ink">J'ai trouvé son site</button>}
          <button onClick={dnc} className="min-h-[44px] text-red-700 underline underline-offset-2">Ne plus contacter</button>
        </div>
      )}
      {showFound && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input value={foundUrl} onChange={(e) => setFoundUrl(e.target.value)} placeholder="https://www.exemple.fr" className="field w-full sm:w-auto sm:min-w-[240px] sm:flex-1" aria-label="URL du site" />
          <button onClick={foundSite} className="btn-primary btn-sm">Valider ce site</button>
          <p className="w-full text-xs text-muted">Le site est vérifié (SIRET, adresse…) avant d'être retenu : il n'est jamais accepté sur parole.</p>
        </div>
      )}
    </section>
  );
}
