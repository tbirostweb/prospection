"use client";
import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { LocalRow, SIGNAL_ICONS, SITE_KIND_DOT, formatPhone, simpleStatus, siteInfo, strongSignals, verifiedSocials } from "@/lib/local";
import { safeHref } from "@/lib/security";

const name = (p: LocalRow) => (p.trade_name || p.company_name).replace(/\s+/g, " ");
/** « A-t-il un site ? » : point de couleur + libellé, avec le lien quand il y en a un. */
export function SiteBadge({ p }: { p: LocalRow }) {
  const s = siteInfo(p);
  return (
    <span className="inline-flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
      <span className="inline-flex items-center gap-1.5 text-ink/80"><span className={`dot ${SITE_KIND_DOT[s.kind]}`} aria-hidden />{s.label}</span>
      {safeHref(s.url) && <a href={safeHref(s.url)!} target="_blank" rel="noopener noreferrer" className="min-w-0 break-all text-accent hover:underline">
        {String(s.url).replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")} ↗</a>}
    </span>
  );
}

/** Moyens de contact en 1 clic : appel, e-mail, formulaire / page contact, réseaux vérifiés. */
export function ContactLinks({ p }: { p: LocalRow }) {
  const socials = verifiedSocials(p);
  const link = "inline-flex min-h-[36px] max-w-full items-center gap-1.5 rounded-full bg-paper px-3 text-[13px] font-medium text-ink hover:bg-line2";
  const items = [
    p.phone && <a key="tel" href={`tel:${p.phone}`} className={link}>☎ {formatPhone(p.phone)}</a>,
    p.email && <a key="mail" href={`mailto:${p.email}`} className={`${link} break-all`}>✉ {p.email}</a>,
    safeHref(p.contact_page) && <a key="form" href={safeHref(p.contact_page)!} target="_blank" rel="noopener noreferrer" className={link}>{p.contact_form ? "Formulaire" : "Page contact"} ↗</a>,
    ...socials.filter((x) => safeHref(x.url)).map((x) => <a key={x.url} href={safeHref(x.url)!} target="_blank" rel="noopener noreferrer" className={link}>{x.network} ↗</a>),
  ].filter(Boolean);
  if (!items.length) return <span className="text-sm text-muted">Aucun contact trouvé</span>;
  return <div className="flex flex-wrap gap-1.5">{items}</div>;
}

/** Le déclencheur fort le plus net (reprise, ouverture récente sans site, site en panne…) : pourquoi écrire MAINTENANT. Rien sinon. */
export function TriggerLine({ p }: { p: LocalRow }) {
  const s = strongSignals(p)[0];
  if (!s) return null;
  return (
    <div className="mt-2 text-sm font-medium text-accent">
      <span aria-hidden>{SIGNAL_ICONS[s.code] ?? "⚡"} </span>{s.label}
      {s.detail && <span className="font-normal text-muted"> · {s.detail}</span>}
    </div>
  );
}

/** Une entreprise sur une ligne : nom, activité et ville, déclencheur fort, site oui/non, contacts en 1 clic, statut, actions rapides facultatives. */
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
    <article className={`row-item py-5 ${done ? "opacity-60" : ""}`}>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <Link href={`/local/${p.id}`} className="break-words text-base font-semibold tracking-[-0.01em] hover:text-accent">{name(p)}</Link>
            <span className="chip">{done ?? status}</span>
          </div>
          <div className="mt-0.5 text-sm text-muted">{[p.activity_label ?? "Activité non classée", p.city].filter(Boolean).join(" · ")}</div>
          <TriggerLine p={p} />
          <div className="mt-2.5"><SiteBadge p={p} /></div>
          <div className="mt-3"><ContactLinks p={p} /></div>
          {actions && !done && !p.do_not_contact && (
            <div className="mt-4 flex flex-wrap gap-2">
              <button disabled={busy} onClick={() => patch({ status: "CONTACTED" }, "Contacté")} className="btn-primary btn-sm">Marquer contacté</button>
              <button disabled={busy} onClick={() => patch({ status: "LOST", response_status: "NOT_A_FIT" }, "Pas intéressant")} className="btn-ghost btn-sm">Pas intéressant</button>
            </div>
          )}
        </div>
        <Link href={`/local/${p.id}`} className="row-arrow" aria-label={`Ouvrir la fiche de ${name(p)}`}>→</Link>
      </div>
    </article>
  );
}

/** Liste d'entreprises : une carte, lignes séparées par des filets fins. */
export function RowList({ children }: { children: React.ReactNode }) {
  return <div className="row-list">{children}</div>;
}

/** En-tête de page : petit libellé facultatif, titre simple, une phrase d'aide, actions secondaires éventuelles. */
export function PageHeader({ title, kicker, intro, children }: { title: string; kicker?: string; intro?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <header className="page-header flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
      <div className="min-w-0">
        {kicker && <span className="page-kicker">{kicker}</span>}
        <h1 className="page-title">{title}</h1>
        {intro && <p className="page-intro">{intro}</p>}
      </div>
      {children && <div className="flex flex-wrap gap-2">{children}</div>}
    </header>
  );
}

/** Chiffre clé (carte). `featured` : la carte vedette, en dégradé orange (une seule par écran). */
export function StatCard({ label, value, hint, href, featured = false }: { label: string; value: React.ReactNode; hint?: React.ReactNode; href?: string; featured?: boolean }) {
  const body = (
    <>
      <span className="mono block leading-tight">{label}</span>
      <span className="big-number mt-3 block text-[1.75rem] sm:mt-4 sm:text-[2.5rem]">{value}</span>
      {hint && <span className={`mt-2 hidden text-[13px] sm:block ${featured ? "text-white/90" : "text-muted"}`}>{hint}</span>}
    </>
  );
  const cls = `stat-block block !p-3.5 sm:!p-5 ${featured ? "stat-block-accent" : ""} ${href ? "transition-transform hover:-translate-y-0.5" : ""}`;
  return href ? <Link href={href} className={cls}>{body}</Link> : <div className={cls}>{body}</div>;
}
