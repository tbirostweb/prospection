import Link from "next/link";
import { PageHeader, StatCard } from "@/components/LocalUI";
import AutoSendButton from "@/components/AutoSendButton";
import { getSetting, safeRows } from "@/lib/localdb";
import { AUTO_DEFAULTS, AUTO_STATES, AutoSend, EMAIL_KINDS } from "@/lib/autosend";
import type { Sender } from "@/lib/draft";
import { dateTimeFr } from "@/lib/format";

export const dynamic = "force-dynamic";

const DAY = 86400000;
const name = (p: any) => String(p.trade_name || p.company_name || "").replace(/\s+/g, " ");
const j = (v: any) => (typeof v === "string" ? (() => { try { return JSON.parse(v); } catch { return null; } })() : v);

/** Minuit (heure de Paris) du jour, en UTC : début du plafond quotidien, comme mailer.paris_midnight_utc. */
function parisMidnightUtc(): Date {
  const day = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Paris", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const guess = new Date(`${day}T00:00:00Z`);
  const offset = new Date(guess.toLocaleString("en-US", { timeZone: "Europe/Paris" })).getTime() - new Date(guess.toLocaleString("en-US", { timeZone: "UTC" })).getTime();
  return new Date(guess.getTime() - offset);
}

/** Prochaine étape d'une séquence en cours : relance J+3, relance J+7, puis abandon à J+14 (worker/local/outbox.py). */
function nextStep(p: any): string {
  const t0 = new Date(p.contacted_at).getTime();
  const n = Number(p.followups ?? 0);
  const [label, at] = n === 0 ? ["Relance J+3", t0 + 3 * DAY] : n === 1 ? ["Relance J+7", t0 + 7 * DAY] : ["Abandon J+14", t0 + 14 * DAY];
  return `${label} · ${at <= Date.now() ? "au prochain créneau" : `à partir du ${dateTimeFr(new Date(at))}`}`;
}

/** Même rendu que la signature HTML du worker (worker/local/mailer.py, signature_html) : à garder identiques. */
function SignaturePreview({ s }: { s: Sender & { logo_url?: string } }) {
  const site = String(s.website ?? "").trim();
  const role = [s.role, s.company].filter(Boolean).join(" · ");
  return (
    <table cellPadding={0} cellSpacing={0} style={{ fontFamily: "Arial,Helvetica,sans-serif", fontSize: 13, lineHeight: 1.45, color: "#231F20" }}>
      <tbody><tr>
        {s.logo_url?.startsWith("https://") && <td style={{ padding: "0 12px 0 0", verticalAlign: "middle" }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={s.logo_url} width={48} height={48} alt={s.company || s.name || ""} style={{ display: "block", width: 48, height: 48, borderRadius: 8 }} /></td>}
        <td style={{ borderLeft: "2px solid #F0451E", padding: "0 0 0 12px", verticalAlign: "middle" }}>
          <strong style={{ fontSize: 14 }}>{s.name || "[ton nom]"}</strong>
          {role && <><br /><span style={{ color: "#8A8580" }}>{role}</span></>}
          {s.phone && <><br /><span>{s.phone}</span></>}
          {site && <><br /><span style={{ color: "#F0451E" }}>{site.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")}</span></>}
        </td>
      </tr></tbody>
    </table>
  );
}

/** Suivi de l'envoi automatique : ce qui est parti, ce qui va partir, qui a répondu, et si tout est prêt pour envoyer. */
export default async function Envois() {
  const auto = { ...AUTO_DEFAULTS, ...(await getSetting<Partial<AutoSend>>("local_auto_send", {})) };
  const sender = await getSetting<Sender & { logo_url?: string }>("local_sender", {});
  const [state] = await safeRows<any>("SELECT * FROM local_mail_state WHERE id=1");
  const checks = j(state?.checks) ?? {};
  const queued = await safeRows<any>(`SELECT id, company_name, trade_name, city, email, auto_queued_at FROM local_prospects
    WHERE auto_state='queued' ORDER BY auto_queued_at, id LIMIT 200`);
  const active = await safeRows<any>(`SELECT id, company_name, trade_name, city, email, contacted_at, followups FROM local_prospects
    WHERE auto_state='active' ORDER BY contacted_at LIMIT 200`);
  const ended = await safeRows<any>(`SELECT id, company_name, trade_name, city, auto_state, last_prospect_contact_at, last_contacted_at FROM local_prospects
    WHERE auto_state IN ('replied','opted_out','bounced','abandoned','failed') ORDER BY COALESCE(last_prospect_contact_at, last_contacted_at) DESC LIMIT 50`);
  const emails = await safeRows<any>(`SELECT e.kind, e.to_email, e.status, e.error, e.sent_at, p.id, p.company_name, p.trade_name FROM local_emails e
    JOIN local_prospects p ON p.id = e.prospect_id ORDER BY e.sent_at DESC LIMIT 60`);
  const [today] = await safeRows<any>("SELECT COUNT(*) AS n FROM local_emails WHERE status IN ('sent','bounced') AND sent_at >= ?", [parisMidnightUtc()]);
  const sentToday = Number(today?.n ?? 0);
  const replied = ended.filter((p) => p.auto_state === "replied").length;

  const blockers: string[] = [];
  if (!auto.enabled) blockers.push("L'envoi automatique est désactivé (Réglages > Envoi automatique).");
  for (const k of checks.config_missing ?? []) blockers.push(`Variable ${k} à renseigner dans Dokploy (service worker).`);
  for (const pb of checks.dns?.problems ?? []) blockers.push(`DNS : ${pb}.`);
  if (!sender.name) blockers.push("Ton nom manque dans Réglages > Mon identité.");
  if (!sender.privacy_url) blockers.push("Le lien de ta notice d'information manque dans Réglages > Mon identité.");
  const warnings: string[] = [...(checks.dns?.warnings ?? [])];
  if (!sender.logo_url) warnings.push("Pas de logo dans la signature (Réglages > Mon identité).");

  return (
    <div className="max-w-4xl">
      <PageHeader title="Envois" intro="Les e-mails envoyés automatiquement, les relances prévues et les réponses. Une réponse arrête aussitôt les relances.">
        <Link href="/settings" className="btn-ghost btn-sm">Réglages d'envoi</Link>
      </PageHeader>

      <div className="stat-grid mb-8 grid-cols-2 !gap-2 sm:grid-cols-4 sm:!gap-4">
        <StatCard featured label="Envoyés aujourd'hui" value={`${sentToday} / ${auto.daily_cap}`} hint={auto.enabled ? `de ${auto.start_hour} h à ${auto.end_hour} h, jours ouvrés` : "envoi désactivé"} />
        <StatCard label="En file" value={queued.length} hint="premiers messages à envoyer" />
        <StatCard label="En cours" value={active.length} hint="relances J+3 / J+7 prévues" />
        <StatCard label="Réponses" value={replied} hint="relances arrêtées" />
      </div>

      <section className={`panel text-sm ${blockers.length ? "border-amber-300 bg-amber-50" : ""}`}>
        <h2 className="section-title">{blockers.length ? "Rien ne part pour l'instant" : "Prêt à envoyer"}</h2>
        {blockers.length > 0 && <ul className="list-disc space-y-1 pl-5">{blockers.map((b) => <li key={b}>{b}</li>)}</ul>}
        {!blockers.length && <p>Boîte d'envoi configurée, DNS vérifié{checks.dns?.at ? ` (${dateTimeFr(checks.dns.at)})` : ""}.</p>}
        {warnings.length > 0 && <ul className="mt-2 space-y-1 text-muted">{warnings.map((w) => <li key={w}>· {w}</li>)}</ul>}
        {state?.last_error && <p className="mt-2 text-red-700">Dernière erreur ({dateTimeFr(state.last_error_at)}) : {state.last_error}</p>}
        {state?.imap_at && <p className="mt-2 text-xs text-muted">Boîte de réception lue le {dateTimeFr(state.imap_at)} (lecture seule).</p>}
        <div className="mt-4 border-t border-line pt-4">
          <span className="mono mb-2 block">Aperçu de la signature</span>
          <p className="mb-2">Bien cordialement,</p>
          <SignaturePreview s={sender} />
        </div>
      </section>

      {queued.length > 0 && (
        <section className="mb-8">
          <h2 className="section-title">En file · {queued.length}</h2>
          <div className="row-list">{queued.map((p) => (
            <div key={p.id} className="row-item flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
              <div className="min-w-0"><Link href={`/local/${p.id}`} className="font-semibold hover:text-accent">{name(p)}</Link>
                <span className="text-muted"> · {p.email} · en file depuis le {dateTimeFr(p.auto_queued_at)}</span></div>
              <AutoSendButton id={p.id} action="auto_stop" />
            </div>))}</div>
        </section>
      )}

      {active.length > 0 && (
        <section className="mb-8">
          <h2 className="section-title">En cours · {active.length}</h2>
          <div className="row-list">{active.map((p) => (
            <div key={p.id} className="row-item flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
              <div className="min-w-0"><Link href={`/local/${p.id}`} className="font-semibold hover:text-accent">{name(p)}</Link>
                <span className="text-muted"> · envoyé le {dateTimeFr(p.contacted_at)} · {nextStep(p)}</span></div>
              <AutoSendButton id={p.id} action="auto_stop" />
            </div>))}</div>
        </section>
      )}

      {ended.length > 0 && (
        <section className="mb-8">
          <h2 className="section-title">Terminés</h2>
          <div className="row-list">{ended.map((p) => (
            <div key={p.id} className="row-item py-3 text-sm">
              <Link href={`/local/${p.id}`} className="font-semibold hover:text-accent">{name(p)}</Link>
              <span className="text-muted"> · {AUTO_STATES[p.auto_state] ?? p.auto_state}{p.last_prospect_contact_at ? ` · ${dateTimeFr(p.last_prospect_contact_at)}` : ""}</span>
            </div>))}</div>
        </section>
      )}

      <section className="mb-8">
        <h2 className="section-title">Derniers e-mails</h2>
        <div className="row-list">
          {emails.map((e, i) => (
            <div key={i} className="row-item py-3 text-sm">
              <span className="text-muted">{dateTimeFr(e.sent_at)} · </span>
              <Link href={`/local/${e.id}`} className="font-semibold hover:text-accent">{name(e)}</Link>
              <span className="text-muted"> · {EMAIL_KINDS[e.kind] ?? e.kind} · {e.to_email}</span>
              {e.status !== "sent" && <span className="text-red-700"> · {e.status === "bounced" ? "revenu (adresse invalide)" : `échec${e.error ? ` : ${e.error}` : ""}`}</span>}
            </div>))}
          {emails.length === 0 && <div className="px-6 py-10 text-center text-muted">Aucun e-mail envoyé pour l'instant. Lance <code>/rediger</code> dans Claude Code pour remplir la file.</div>}
        </div>
      </section>
    </div>
  );
}
