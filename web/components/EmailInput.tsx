"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

/** « Ajouter l'e-mail » : l'adresse que tu as trouvée toi-même (page Facebook, carte de visite…). Elle rend la fiche envoyable aussitôt. */
export default function EmailInput({ id, compact = false }: { id: number; compact?: boolean }) {
  const router = useRouter();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    const r = await fetch(`/api/local/prospects/${id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "set_email", email: value }) });
    if (!r.ok) setErr((await r.json().catch(() => ({}))).error ?? "refusé");
    else setValue("");
    setBusy(false);
    router.refresh();
  }
  return (
    <form onSubmit={save} className="inline-flex flex-wrap items-center gap-2">
      <input type="email" required value={value} onChange={(e) => setValue(e.target.value)} placeholder="contact@…" aria-label="Adresse e-mail"
        className={`field ${compact ? "!min-h-0 w-48 !py-1.5 text-xs" : "w-64"}`} />
      <button disabled={busy || !value} className="btn-ghost btn-sm">Ajouter l'e-mail</button>
      {err && <span className="text-xs text-red-700">{err}</span>}
    </form>
  );
}
