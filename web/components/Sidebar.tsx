"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

// Cinq entrées seulement. Carte et tournée sont accessibles depuis « Entreprises » et « À contacter », les statistiques depuis « Réglages ».
const NAV = [
  { href: "/local/contact", label: "À contacter", num: "01", also: ["/local/tournee"] },
  { href: "/local/suivi", label: "Suivi", num: "02", also: [] },
  { href: "/local", label: "Entreprises", num: "03", also: ["/local/carte"] },
  { href: "/local/campagnes", label: "Campagnes", num: "04", also: [] },
  { href: "/settings", label: "Réglages", num: "05", also: ["/local/stats"] },
];
const CTA = { href: "/local/campagnes", label: "Nouvelle campagne" };

function isActive(pathname: string, n: (typeof NAV)[number]) {
  if (n.href === "/local") return pathname === "/local" || /^\/local\/\d+/.test(pathname) || n.also.some((a) => pathname.startsWith(a));
  return pathname === "/" && n.href === "/local/contact" || pathname.startsWith(n.href) || n.also.some((a) => pathname.startsWith(a));
}

function Logo() {
  return (
    <Link href="/local/contact" className="inline-flex min-h-[44px] items-center gap-2 text-ink">
      <span className="font-display text-2xl font-bold uppercase leading-none tracking-[-0.02em]">Prospection</span>
      <span className="h-2.5 w-2.5 bg-signal" aria-hidden />
    </Link>
  );
}

/** Barre de navigation fine en haut : liens en petites capitales, action principale en aplat orange collée à droite. */
export default function Sidebar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [pathname]);
  const current = NAV.find((n) => isActive(pathname, n));

  return (
    <header className="sticky top-0 z-[1000] border-b-[3px] border-ink bg-surface">
      <div className="flex items-stretch justify-between">
        <div className="flex items-center px-4 sm:px-6 md:px-10 xl:border-r-2 xl:border-ink"><Logo /></div>

        {/* Desktop : liens en ligne */}
        <nav className="hidden min-w-0 flex-1 items-stretch overflow-hidden xl:flex" aria-label="Navigation principale">
          {NAV.map((n) => {
            const active = isActive(pathname, n);
            return (
              <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined}
                className={`flex min-h-[56px] items-center gap-2 border-r-2 border-ink px-4 whitespace-nowrap font-mono text-[12px] font-medium uppercase tracking-[0.12em] transition-colors 2xl:px-6 ${
                  active ? "bg-ink text-surface" : "text-ink hover:bg-ink hover:text-surface"}`}>
                <span className={active ? "text-accent-light" : "text-muted"}>{n.num}</span>
                <span>{n.label}</span>
              </Link>
            );
          })}
        </nav>
        <form action="/api/auth/logout" method="post" className="hidden xl:flex">
          <button type="submit" title="Se déconnecter"
            className="flex min-h-[56px] items-center gap-2 border-l-2 border-ink px-4 font-mono text-[12px] font-medium uppercase tracking-[0.12em] whitespace-nowrap text-ink transition-colors hover:bg-ink hover:text-surface">
            Déconnexion <span aria-hidden className="text-base leading-none">⏻</span>
          </button>
        </form>
        <Link href={CTA.href}
          className="hidden items-center gap-3 bg-accent px-6 font-mono text-[12px] font-semibold uppercase tracking-[0.12em] whitespace-nowrap text-white transition-colors hover:bg-ink xl:flex">
          {CTA.label} <span aria-hidden className="text-lg leading-none">↗</span>
        </Link>

        {/* Mobile, tablette et petit portable : bouton Menu (aucun défilement horizontal) */}
        <div className="flex items-center px-4 py-1.5 sm:px-6 xl:hidden">
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls="mobile-nav"
            className="inline-flex min-h-[44px] items-center gap-2 border-2 border-ink px-4 font-mono text-xs font-semibold uppercase tracking-wide hover:bg-ink hover:text-surface">
            <span className="max-w-[9rem] truncate">{open ? "Fermer" : current?.label ?? "Menu"}</span>
            <span aria-hidden>{open ? "×" : "☰"}</span>
          </button>
        </div>
      </div>
      {open && (
        <nav id="mobile-nav" className="border-t-2 border-ink xl:hidden" aria-label="Navigation principale">
          {NAV.map((n) => {
            const active = isActive(pathname, n);
            return (
              <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined}
                className={`flex min-h-[52px] items-center gap-3 border-b-2 border-ink px-4 font-mono text-sm uppercase tracking-wide sm:px-6 ${
                  active ? "bg-ink font-semibold text-surface" : "text-ink"}`}>
                <span className={`text-[11px] ${active ? "text-accent-light" : "text-muted"}`}>{n.num}</span>
                {n.label}
                <span className="ml-auto" aria-hidden>→</span>
              </Link>
            );
          })}
          <Link href={CTA.href} className="flex min-h-[52px] items-center justify-between bg-accent px-4 font-mono text-sm font-semibold uppercase tracking-wide text-white sm:px-6">
            {CTA.label} <span aria-hidden className="text-lg">↗</span>
          </Link>
          <form action="/api/auth/logout" method="post">
            <button type="submit"
              className="flex min-h-[52px] w-full items-center justify-between border-b-2 border-ink bg-surface px-4 font-mono text-sm font-semibold uppercase tracking-wide text-ink hover:bg-ink hover:text-surface sm:px-6">
              Se déconnecter <span aria-hidden>⏻</span>
            </button>
          </form>
        </nav>
      )}
    </header>
  );
}
