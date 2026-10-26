"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

/** « Tout mettre en file » : met en file d'envoi automatique tous les brouillons éligibles de la liste, un par un (le serveur revérifie chacun). */
export default function BulkQueueButton({ ids }: { ids: number[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  async function go() {
    setBusy(true); setDone(null);
    let ok = 0;
    for (const id of ids) {
      const r = await fetch(`/api/local/prospects/${id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "auto_queue" }) });
      if (r.ok) ok++;
    }
    setDone(`${ok} mis en file${ok < ids.length ? `, ${ids.length - ok} refusé(s)` : ""}`);
    setBusy(false);
    router.refresh();
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button disabled={busy || !ids.length} onClick={go} className="btn-primary btn-sm no-arrow">{busy ? "Mise en file…" : `Tout mettre en file (${ids.length})`}</button>
      {done && <span className="text-xs text-muted">{done}</span>}
    </span>
  );
}
