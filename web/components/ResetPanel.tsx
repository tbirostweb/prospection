"use client";
import { useEffect, useState } from "react";

// Bouton « Tout effacer » : pose une demande que le worker exécute dans la minute (sous son verrou).
export default function ResetPanel() {
  const [info, setInfo] = useState<any>(null);
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [relaunch, setRelaunch] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = () => fetch("/api/local/reset").then((r) => r.json()).then(setInfo).catch(() => {});
  useEffect(() => { load(); }, []);
  useEffect(() => {                                   // demande en attente : on suit son exécution
    if (!info?.pending) return;
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [info?.pending]);

  async function submit() {
    setBusy(true);
    await fetch("/api/local/reset", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ confirm, relaunch }) });
    setBusy(false); setOpen(false); setConfirm(""); load();
  }
  async function cancel() { await fetch("/api/local/reset", { method: "DELETE" }); load(); }

  if (!info) return null;
  const fmt = (d: string) => new Date(d).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });

  return (
    <section className="mb-6 border-t border-red-700/60 pt-5">
      <h2 className="section-title">Remise à zéro des entreprises</h2>
      <p className="mb-3 text-sm text-muted">
        Efface toutes les entreprises trouvées ({info.prospects}) pour repartir de zéro avec le nouveau barème. Tes campagnes sont
        relancées et retrouvent les entreprises avec les notes à jour.
      </p>
      <p className="mb-4 text-sm text-muted">
        <b>Toujours conservé :</b> la liste « ne plus contacter » ({info.dnc}), les mauvais sites signalés, tes corrections de site,
        tes réglages et tes campagnes.
      </p>

      {info.pending ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg bg-amber-50 p-4 text-sm">
          <span>⏳ Remise à zéro demandée le {fmt(info.pending.requested_at)} : exécution dans la minute.</span>
          <button onClick={cancel} className="btn-ghost btn-sm">Annuler</button>
        </div>
      ) : !open ? (
        <button onClick={() => setOpen(true)} disabled={!info.prospects} className="btn-ghost !border-red-300 !text-red-700">🧹 Tout effacer et repartir de zéro</button>
      ) : (
        <div className="space-y-3 rounded-lg bg-red-50 p-4 text-sm">
          {info.worked > 0 && (
            <p className="font-medium text-red-800">⚠️ {info.worked} entreprise(s) ont un suivi (contactée, réponse, client…) : ce suivi sera perdu aussi.</p>
          )}
          <label className="check"><input type="checkbox" checked={relaunch} onChange={(e) => setRelaunch(e.target.checked)} /> Relancer toutes mes campagnes ensuite</label>
          <label className="block">Tape <b>EFFACER</b> pour confirmer :
            <input value={confirm} onChange={(e) => setConfirm(e.target.value)} className="field mt-1 w-40" autoFocus /></label>
          <div className="flex flex-wrap gap-2">
            <button onClick={submit} disabled={confirm !== "EFFACER" || busy} className="btn-primary no-arrow !bg-red-700 disabled:opacity-40">Effacer {info.prospects} entreprise(s)</button>
            <button onClick={() => { setOpen(false); setConfirm(""); }} className="btn-ghost">Annuler</button>
          </div>
        </div>
      )}
      {info.last && !info.pending && (
        <p className="mt-3 text-xs text-muted">Dernière remise à zéro : {fmt(info.last.done_at)} — {info.last.prospects} entreprise(s) effacée(s){info.last.relaunched ? `, ${info.last.relaunched} campagne(s) relancée(s)` : ""}.</p>
      )}
    </section>
  );
}
