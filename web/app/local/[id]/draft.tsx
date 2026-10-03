"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { mailtoLink } from "@/lib/draft";

/** Brouillon d'e-mail : créé UNIQUEMENT sur clic, modifiable, jamais envoyé par l'application. « Ouvrir dans ma messagerie » prépare le mail : c'est toi qui l'envoies. */
export default function DraftBox({ id, email, subject, body, createdAt, doNotContact }: {
  id: number; email: string | null; subject: string | null; body: string | null; createdAt: string | null; doNotContact: boolean;
}) {
  const router = useRouter();
  const [subj, setSubj] = useState(subject ?? "");
  const [text, setText] = useState(body ?? "");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [warn, setWarn] = useState<string | null>(null);
  if (doNotContact) return null;

  async function create() {
    if (text && !confirm("Remplacer le brouillon actuel (et tes modifications) par un nouveau brouillon ?")) return;
    setBusy(true); setMsg(null);
    const res = await fetch(`/api/local/prospects/${id}/draft`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) { setMsg(data.error ?? "Refusé"); return; }
    setSubj(data.subject); setText(data.body);
    setWarn(data.senderMissing ? "Renseigne ton nom et tes coordonnées dans Réglages → « Mon identité » pour une signature complète." : null);
    router.refresh();
  }
  async function save(extra: any = {}) {
    const res = await fetch(`/api/local/prospects/${id}`, { method: "PATCH", headers: { "content-type": "application/json" },
      body: JSON.stringify({ draft_subject: subj, draft_message: text, ...extra }) });
    setMsg(res.ok ? "Enregistré" : "Refusé");
    router.refresh();
  }
  async function remove() {
    if (!confirm("Supprimer ce brouillon ?")) return;
    await fetch(`/api/local/prospects/${id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ draft_message: null }) });
    setSubj(""); setText(""); router.refresh();
  }

  return (
    <section className="panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="section-title !mb-0">Message (brouillon d'e-mail)</h2>
        <button onClick={create} disabled={busy} className={text ? "btn-ghost btn-sm" : "btn-primary btn-sm"}>{busy ? "…" : text ? "Recréer le brouillon" : "Créer un brouillon"}</button>
      </div>
      {!text && <p className="mt-2 text-xs text-muted">Un e-mail court et personnalisé, construit uniquement avec les faits détectés sur cette entreprise. Rien n'est envoyé : tu relis, tu corriges, tu l'envoies toi-même.</p>}
      {text && (
        <div className="mt-3 grid gap-2">
          {createdAt && <div className="mono !text-[10px]">Créé le {new Date(createdAt).toLocaleString("fr-FR")}</div>}
          <label className="flex flex-col gap-1 text-sm"><span className="mono">Objet</span>
            <input value={subj} onChange={(e) => setSubj(e.target.value)} className="field" /></label>
          <label className="flex flex-col gap-1 text-sm"><span className="mono">Message</span>
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={14} className="field w-full text-sm" /></label>
          <div className="flex flex-wrap items-center gap-2">
            <a href={mailtoLink(email, subj, text)} onClick={() => save()} className="btn-primary btn-sm max-w-full break-all">{email ? `Ouvrir dans ma messagerie (${email})` : "Ouvrir dans ma messagerie"}</a>
            <button onClick={() => { navigator.clipboard?.writeText(`${subj}\n\n${text}`); setMsg("Copié"); }} className="btn-ghost btn-sm">Copier</button>
            <button onClick={() => save()} className="btn-ghost btn-sm">Enregistrer</button>
            <button onClick={() => save({ status: "CONTACTED" })} className="btn-ghost btn-sm">Marquer contacté</button>
            <button onClick={remove} className="btn-ghost btn-sm !text-red-700">Supprimer</button>
            {msg && <span className="text-sm text-muted">{msg}</span>}
          </div>
          {!email && <p className="text-xs text-amber-900">Aucune adresse e-mail professionnelle connue pour cette entreprise : copie le message dans un formulaire de contact, ou sers-t'en comme trame d'appel.</p>}
        </div>
      )}
      {warn && <p className="mt-2 text-xs text-amber-900">{warn}</p>}
    </section>
  );
}
