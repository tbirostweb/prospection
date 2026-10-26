"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

// Cinq entrées seulement. Carte et tournée sont accessibles depuis « Entreprises » et « À contacter », les statistiques depuis « Réglages ».
const NAV = [
  { href: "/local/contact", label: "À contacter", also: ["/local/tournee", "/local/import"] },
  { href: "/local/suivi", label: "Suivi", also: [] },
  { href: "/local/envois", label: "Envois", also: [] },
  { href: "/local", label: "Entreprises", also: ["/local/carte"] },
  { href: "/local/campagnes", label: "Campagnes", also: [] },
  { href: "/settings", label: "Réglages", also: ["/local/stats"] },
];
const CTA = { href: "/local/campagnes", label: "Nouvelle campagne" };

function isActive(pathname: string, n: (typeof NAV)[number]) {
  if (n.href === "/local") return pathname === "/local" || /^\/local\/\d+/.test(pathname) || n.also.some((a) => pathname.startsWith(a));
  return pathname === "/" && n.href === "/local/contact" || pathname.startsWith(n.href) || n.also.some((a) => pathname.startsWith(a));
}

function Logo() {
  return (
    <Link href="/local/contact" className="inline-flex min-h-[44px] min-w-0 items-center gap-2.5 rounded-full bg-surface py-1.5 pl-1.5 pr-4 shadow-soft">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-signal text-sm font-bold text-white" aria-hidden>P</span>
      <span className="truncate text-[15px] font-semibold tracking-[-0.01em]">Prospection</span>
    </Link>
  );
}

function LogoutIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="m16 17 5-5-5-5" /><path d="M21 12H9" />
    </svg>
  );
}

/** Barre du haut : logo, navigation en pilule (onglet actif en noir), action principale et déconnexion. */
export default function Sidebar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [pathname]);
  const current = NAV.find((n) => isActive(pathname, n));

  return (
    <header className="sticky top-0 z-[1000] bg-canvas/85 px-4 py-3 backdrop-blur sm:px-6 md:px-10">
      <div className="mx-auto flex max-w-content items-center justify-between gap-3">
        <Logo />

        {/* Écran large : onglets en pilule */}
        <nav className="hidden items-center gap-1 rounded-full bg-surface p-1 shadow-soft lg:flex" aria-label="Navigation principale">
          {NAV.map((n) => {
            const active = isActive(pathname, n);
            return (
              <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined}
                className={`inline-flex min-h-[40px] items-center rounded-full px-4 text-sm font-medium whitespace-nowrap transition-colors ${
                  active ? "bg-ink text-white" : "text-ink/70 hover:bg-paper hover:text-ink"}`}>
                {n.label}
              </Link>
            );
          })}
        </nav>

        <div className="hidden items-center gap-2 lg:flex">
          <Link href={CTA.href} className="btn-primary">{CTA.label}</Link>
          <form action="/api/auth/logout" method="post">
            <button type="submit" title="Se déconnecter" aria-label="Se déconnecter"
              className="grid h-11 w-11 place-items-center rounded-full bg-surface text-ink/70 shadow-soft transition-colors hover:text-ink">
              <LogoutIcon />
            </button>
          </form>
        </div>

        {/* Mobile et tablette : bouton Menu (aucun défilement horizontal) */}
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls="mobile-nav"
          className="inline-flex min-h-[44px] shrink-0 items-center gap-2 rounded-full bg-surface px-4 text-sm font-medium shadow-soft lg:hidden">
          <span className="max-w-[9rem] truncate">{open ? "Fermer" : current?.label ?? "Menu"}</span>
          <span aria-hidden className="text-base leading-none">{open ? "×" : "☰"}</span>
        </button>
      </div>
      {open && (
        <nav id="mobile-nav" className="mx-auto mt-3 max-w-content rounded-[var(--radius-card)] bg-surface p-2 shadow-soft lg:hidden" aria-label="Navigation principale">
          {NAV.map((n) => {
            const active = isActive(pathname, n);
            return (
              <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined}
                className={`flex min-h-[48px] items-center justify-between rounded-2xl px-4 text-[15px] font-medium ${active ? "bg-ink text-white" : "text-ink hover:bg-paper"}`}>
                {n.label}<span aria-hidden className={active ? "text-white/70" : "text-muted"}>→</span>
              </Link>
            );
          })}
          <div className="mt-2 flex flex-wrap gap-2 border-t border-line2 px-2 pt-3">
            <Link href={CTA.href} className="btn-primary flex-1">{CTA.label}</Link>
            <form action="/api/auth/logout" method="post" className="flex-1">
              <button type="submit" className="btn-ghost w-full">Se déconnecter</button>
            </form>
          </div>
        </nav>
      )}
    </header>
  );
}
