"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

/** Relance due : « Relancé » (J+3, J+10), « Réponse reçue » ou « Sans réponse » (compté comme un échec par l'apprentissage). Rien n'est envoyé. */
export default function FollowupButtons({ id, kind }: { id: number; kind: "followup" | "no_reply" }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  async function patch(body: any) {
    setBusy(true);
    await fetch(`/api/local/prospects/${id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    setBusy(false);
    router.refresh();
  }
  return (
    <div className="flex flex-wrap gap-2">
      {kind === "followup" && <button disabled={busy} onClick={() => patch({ action: "followed_up" })} className="btn-primary btn-sm">✓ Relancé</button>}
      <button disabled={busy} onClick={() => patch({ status: "REPLIED", response_status: "REPLIED" })} className="btn-ghost btn-sm">💬 Réponse reçue</button>
      <button disabled={busy} onClick={() => patch({ action: "no_answer" })} className="btn-ghost btn-sm">Sans réponse</button>
    </div>
  );
}
