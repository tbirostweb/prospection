"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

/** Mettre un prospect en file d'envoi automatique, ou arrêter son envoi (plus aucune relance). L'envoi lui-même est fait par le worker. */
export default function AutoSendButton({ id, action }: { id: number; action: "auto_queue" | "auto_stop" }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    setBusy(true); setErr(null);
    const r = await fetch(`/api/local/prospects/${id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ action }) });
    if (!r.ok) setErr((await r.json().catch(() => ({}))).error ?? "refusé");
    setBusy(false);
    router.refresh();
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button disabled={busy} onClick={go} className={action === "auto_queue" ? "btn-primary btn-sm no-arrow" : "btn-ghost btn-sm"}>
        {action === "auto_queue" ? "Mettre en file d'envoi" : "Arrêter l'envoi"}
      </button>
      {err && <span className="text-xs text-red-700">{err}</span>}
    </span>
  );
}
