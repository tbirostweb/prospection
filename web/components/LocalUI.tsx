"use client";
import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { LocalRow, SITE_KIND_CLASS, formatPhone, simpleStatus, siteInfo, verifiedSocials } from "@/lib/local";
import { safeHref } from "@/lib/security";

const name = (p: LocalRow) => (p.trade_name || p.company_name).replace(/\s+/g, " ");
/** Badge « a-t-il un site ? » avec le lien quand il y en a un. */
export function SiteBadge({ p }: { p: LocalRow }) {
  const s = siteInfo(p);
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className={`border px-2 py-0.5 font-mono text-[11px] font-medium uppercase tracking-wide ${SITE_KIND_CLASS[s.kind]}`}>{s.label}</span>
      {safeHref(s.url) && <a href={safeHref(s.url)!} target="_blank" rel="noopener noreferrer" className="break-all font-mono text-xs font-medium text-accent underline underline-offset-2 hover:text-ink">
        {String(s.url).replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")} ↗</a>}
    </span>
  );
}

/** Moyens de contact en 1 clic : appel, e-mail, formulaire / page contact, réseaux vérifiés. */
export function ContactLinks({ p }: { p: LocalRow }) {
  const socials = verifiedSocials(p);
  const items = [
    p.phone && <a key="tel" href={`tel:${p.phone}`} className="btn-ghost btn-sm">☎ {formatPhone(p.phone)}</a>,
    p.email && <a key="mail" href={`mailto:${p.email}`} className="btn-ghost btn-sm max-w-full break-all">✉ {p.email}</a>,
    safeHref(p.contact_page) && <a key="form" href={safeHref(p.contact_page)!} target="_blank" rel="noopener noreferrer" className="btn-ghost btn-sm">▤ {p.contact_form ? "Formulaire" : "Page contact"} ↗</a>,
    ...socials.filter((x) => safeHref(x.url)).map((x) => <a key={x.url} href={safeHref(x.url)!} target="_blank" rel="noopener noreferrer" className="btn-ghost btn-sm">{x.network} ↗</a>),
  ].filter(Boolean);
  if (!items.length) return <span className="font-mono text-xs uppercase tracking-wide text-muted">Aucun contact trouvé</span>;
  return <div className="flex flex-wrap gap-1.5">{items}</div>;
}

/** Une entreprise sur une ligne : nom, activité et ville, site oui/non, contacts en 1 clic, statut, actions rapides facultatives. */
export function CompanyRow({ p, actions = false }: { p: LocalRow; actions?: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  async function patch(body: any, label: string) {
    setBusy(true);
    const res = await fetch(`/api/local/prospects/${p.id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    setBusy(false);
    if (res.ok) { setDone(label); router.refresh(); }
  }
  const status = simpleStatus(p);
  return (
    <article className={`row-item grid grid-cols-[2.75rem_minmax(0,1fr)] gap-x-3 py-6 sm:grid-cols-[4.5rem_minmax(0,1fr)] sm:gap-x-5 ${done ? "opacity-60" : ""}`}>
      <span className="row-num pt-1" aria-hidden />
      <div className="min-w-0">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
              <Link href={`/local/${p.id}`} className="break-words font-display text-2xl font-bold uppercase leading-[0.95] tracking-[-0.01em] hover:text-accent md:text-[1.75rem]">{name(p)}</Link>
              <span className="chip">{done ?? status}</span>
            </div>
            <div className="mt-1.5 font-mono text-xs uppercase tracking-wide text-muted">{[p.activity_label ?? "Activité non classée", p.city].filter(Boolean).join(" · ")}</div>
            <div className="mt-3"><SiteBadge p={p} /></div>
            <div className="mt-3"><ContactLinks p={p} /></div>
          </div>
          <Link href={`/local/${p.id}`} className="row-arrow" aria-label={`Ouvrir la fiche de ${name(p)}`}>→</Link>
        </div>
        {actions && !done && !p.do_not_contact && (
          <div className="mt-4 flex flex-wrap gap-2">
            <button disabled={busy} onClick={() => patch({ status: "CONTACTED" }, "Contacté")} className="btn-primary btn-sm">Marquer contacté</button>
            <button disabled={busy} onClick={() => patch({ status: "LOST", response_status: "NOT_A_FIT" }, "Pas intéressant")} className="btn-ghost btn-sm">Pas intéressant</button>
            <Link href={`/local/${p.id}`} className="btn-ghost btn-sm">Fiche, notes, message</Link>
          </div>
        )}
      </div>
    </article>
  );
}

/** Liste d'entreprises numérotée (01, 02…) : lignes séparées par des filets épais. */
export function RowList({ children }: { children: React.ReactNode }) {
  return <div className="row-list">{children}</div>;
}

/** Titre de page : le dernier mot en orange (un titre d'un seul mot reçoit un point orange). */
function AccentTitle({ title }: { title: string }) {
  const words = title.trim().split(/\s+/);
  if (words.length < 2) return <>{title}<span className="text-signal">.</span></>;
  return <>{words.slice(0, -1).join(" ")} <span className="text-signal">{words[words.length - 1]}</span></>;
}

/** En-tête héroïque commun : kicker, titre condensé énorme, une phrase d'explication, liens secondaires éventuels. */
export function PageHeader({ title, kicker, intro, children }: { title: string; kicker?: string; intro?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <header className="page-header">
      {kicker && <span className="page-kicker">{kicker}</span>}
      <h1 className="page-title"><AccentTitle title={title} /></h1>
      {intro && <p className="page-intro">{intro}</p>}
      {children && <div className="mt-6 flex flex-wrap gap-2">{children}</div>}
    </header>
  );
}
