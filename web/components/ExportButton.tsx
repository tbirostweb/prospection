"use client";
import { useState } from "react";

/** « Exporter (CSV) » : télécharge la liste affichée (mêmes filtres), sans les entreprises en opposition ou exclues. */
export default function ExportButton({ params }: { params: Record<string, string> }) {
  const [emailOnly, setEmailOnly] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setMsg(null);
    try {
      const qs = new URLSearchParams({ ...params, ...(emailOnly ? { email: "1" } : {}) });
      const res = await fetch(`/api/local/export?${qs}`, { cache: "no-store" });
      if (!res.ok) { setMsg(res.status === 401 ? "Session expirée : reconnecte-toi." : "Export impossible pour le moment."); return; }
      const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? "prospects.csv";
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      const n = Number(res.headers.get("x-export-count") ?? 0);
      setMsg(`${n} entreprise${n > 1 ? "s" : ""} exportée${n > 1 ? "s" : ""}`
        + (res.headers.get("x-export-truncated") === "1" ? " — liste tronquée (limite atteinte)." : "."));
    } catch {
      setMsg("Export impossible pour le moment.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <button type="button" onClick={run} disabled={busy} className="btn-ghost btn-sm" title="Sans les entreprises « ne plus contacter » ni exclues">
        {busy ? "Export en cours…" : "Exporter (CSV)"}
      </button>
      <label className="check inline-flex items-center gap-2 text-sm text-muted">
        <input type="checkbox" checked={emailOnly} onChange={(e) => setEmailOnly(e.target.checked)} />
        Avec e-mail seulement
      </label>
      {msg && <span className="w-full text-xs text-muted" aria-live="polite">{msg}</span>}
    </div>
  );
}
