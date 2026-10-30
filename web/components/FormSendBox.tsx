"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

/** Fiche sans e-mail mais avec un formulaire de contact : message prêt à copier, lien vers la page, puis « Envoyé via le formulaire »
 *  (fiche contactée, canal noté, rappel à J+7). Rien n'est envoyé par l'application : tu colles le message dans le formulaire toi-même. */
export default function FormSendBox({ id, body, contactPage, sentAt }: { id: number; body: string | null; contactPage: string; sentAt: string | null }) {
  const router = useRouter();
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function copy() {
    try { await navigator.clipboard.writeText(body ?? ""); setMsg("Copié"); } catch { setMsg("Copie impossible : sélectionne le texte à la main"); }
  }
  async function sent() {
    setBusy(true);
    const res = await fetch(`/api/local/prospects/${id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "form_sent" }) });
    setBusy(false);
    if (!res.ok) { setMsg((await res.json().catch(() => ({}))).error ?? "Refusé"); return; }
    setMsg("Enregistré : rappel dans 7 jours"); router.refresh();
  }
  return (
    <section className="panel">
      <h2 className="section-title">Message pour le formulaire de contact</h2>
      {sentAt ? <p className="text-sm text-muted">Envoyé via le formulaire le {new Date(sentAt).toLocaleDateString("fr-FR")}.</p> : !body ? (
        <p className="text-sm text-muted">Pas encore de message : <code>/rediger N formulaire</code> en prépare pour les fiches sans e-mail (rien ne part seul).</p>
      ) : (
        <>
          <pre className="whitespace-pre-wrap rounded-2xl border border-line bg-surface p-4 text-sm">{body}</pre>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button onClick={copy} className="btn-primary btn-sm">Copier</button>
            <a href={contactPage} target="_blank" rel="noopener noreferrer" className="btn-ghost btn-sm">Ouvrir la page de contact ↗</a>
            <button onClick={sent} disabled={busy} className="btn-ghost btn-sm">Envoyé via le formulaire</button>
            {msg && <span className="text-sm text-muted">{msg}</span>}
          </div>
        </>
      )}
    </section>
  );
}
