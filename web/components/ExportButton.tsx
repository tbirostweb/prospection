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
    <div className="mb-6 flex flex-wrap items-center gap-x-4 gap-y-2">
      <button type="button" onClick={run} disabled={busy} className="btn-ghost min-h-[44px] w-full sm:w-auto">
        {busy ? "Export en cours…" : "Exporter (CSV) ↗"}
      </button>
      <label className="check inline-flex min-h-[44px] items-center gap-2 text-sm">
        <input type="checkbox" checked={emailOnly} onChange={(e) => setEmailOnly(e.target.checked)} />
        Seulement ceux avec e-mail
      </label>
      <span className="w-full text-xs text-muted" aria-live="polite">
        {msg ?? "Sans les entreprises « ne plus contacter » ni exclues."}
      </span>
    </div>
  );
}
