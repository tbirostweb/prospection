"use client";
import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { CALL_MAX_ATTEMPTS } from "@/lib/calls";

export type CallItem = { id: number; name: string; city: string | null; activity: string | null; phone: string; phoneLabel: string; angle: string; angleLabel: string;
  score: number | null; attempts: number; script: string[]; bestTime: string; tomorrow: string };

/** Une fiche à appeler : argumentaire en 3 lignes, puis le résultat de l'appel en un clic. Rien n'est envoyé : tu appelles toi-même. */
export default function CallRow({ p }: { p: CallItem }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [showDate, setShowDate] = useState(false);
  const [date, setDate] = useState(p.tomorrow);
  async function patch(body: any, label: string) {
    setBusy(true); setMsg(null);
    const res = await fetch(`/api/local/prospects/${p.id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    setBusy(false);
    if (!res.ok) { setMsg((await res.json().catch(() => ({}))).error ?? "Refusé"); return; }
    setMsg(label); router.refresh();
  }
  const done = msg !== null && !msg.startsWith("Refusé");
  const call = (result: string, label: string, extra: any = {}) => patch({ action: "call_result", result, ...extra }, label);
  async function dnc() {
    if (!confirm("Marquer « ne plus contacter » ? L'entreprise sera exclue de toute future campagne. Ce choix ne se défait pas depuis l'interface.")) return;
    await patch({ status: "DO_NOT_CONTACT", reason: "demande de ne plus être contactée (téléphone)" }, "Ne plus contacter");
  }
  return (
    <article className={`row-item py-5 ${done ? "opacity-60" : ""}`}>
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <Link href={`/local/${p.id}`} className="break-words text-base font-semibold tracking-[-0.01em] hover:text-accent">{p.name}</Link>
        <span className="chip">{p.angleLabel}</span>
        {p.score != null && <span className="text-xs text-muted">score {p.score}</span>}
        {p.attempts > 0 && <span className="text-xs text-amber-900">{p.attempts} appel{p.attempts > 1 ? "s" : ""} sans réponse sur {CALL_MAX_ATTEMPTS}</span>}
      </div>
      <div className="mt-0.5 text-sm text-muted">{[p.activity ?? "Activité non classée", p.city].filter(Boolean).join(" · ")}</div>
      <a href={`tel:${p.phone}`} className="mt-2 inline-flex min-h-[44px] items-center text-base font-medium text-accent hover:underline md:min-h-8">☎ {p.phoneLabel}</a>
      <div className="text-xs text-muted">Quand appeler : {p.bestTime}</div>
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm text-ink/80">{p.script.map((l, i) => <li key={i}>{l}</li>)}</ol>
      {done ? <p className="mt-3 text-sm text-muted">{msg}</p> : (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button disabled={busy} onClick={() => call("no_answer", "Pas de réponse noté")} className="btn-ghost btn-sm">Pas de réponse</button>
          <button disabled={busy} onClick={() => setShowDate(!showDate)} className="btn-ghost btn-sm">Rappeler</button>
          <button disabled={busy} onClick={() => call("interested", "Intéressé : rappel aujourd'hui")} className="btn-primary btn-sm">Intéressé</button>
          <button disabled={busy} onClick={() => call("not_interested", "Pas intéressé")} className="btn-ghost btn-sm">Pas intéressé</button>
          <button disabled={busy} onClick={dnc} className="btn-ghost btn-sm !text-red-700">Ne plus contacter</button>
          {msg && <span className="text-sm text-red-700">{msg}</span>}
        </div>
      )}
      {showDate && !done && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="field" aria-label="Date du rappel" />
          <button disabled={busy || !date} onClick={() => call("callback", `Rappel le ${date.split("-").reverse().join("/")}`, { date })} className="btn-primary btn-sm">Poser le rappel</button>
        </div>
      )}
    </article>
  );
}
