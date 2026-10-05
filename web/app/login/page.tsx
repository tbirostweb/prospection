import type { Metadata } from "next";
import { safeNextPath } from "@/lib/security";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Connexion · Prospection locale", robots: { index: false, follow: false } };

const ERRORS: Record<string, string> = {
  "1": "Identifiant ou mot de passe incorrect.",
  blocked: "Trop de tentatives. Réessaie dans quelques minutes.",
};

/** Page de connexion plein écran (remplace la boîte HTTP Basic du navigateur). Formulaire HTML classique : fonctionne sans JavaScript. */
export default async function LoginPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);
  const next = safeNextPath(one(sp.next));
  const error = ERRORS[one(sp.error) ?? ""];

  return (
    <div className="flex min-h-screen min-h-[100dvh] flex-col bg-surface text-ink">
      <header className="flex min-h-[56px] items-stretch justify-between border-b-[3px] border-ink">
        <div className="flex items-center gap-2 px-4 sm:px-6 md:px-10">
          <span className="font-display text-2xl font-bold uppercase leading-none tracking-[-0.02em]">Prospection</span>
          <span className="h-2.5 w-2.5 bg-signal" aria-hidden />
        </div>
        <div className="flex items-center border-l-2 border-ink px-4 font-mono text-[11px] font-medium uppercase tracking-[0.12em] sm:px-6 md:px-10">
          Accès privé
        </div>
      </header>

      <main className="grid flex-1 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <section className="flex min-w-0 flex-col justify-between gap-10 px-4 py-10 sm:px-6 md:px-10 md:py-14 lg:border-r-[3px] lg:border-ink">
          <div className="min-w-0" style={{ containerType: "inline-size" }}>
            <span className="page-kicker">/ 00 — Connexion</span>
            <h1 className="font-display font-bold uppercase" style={{ fontSize: "clamp(3rem, 22cqi, 11rem)", lineHeight: 1, letterSpacing: "-0.02em" }}>
              <span className="block whitespace-nowrap">Accès</span>
              <span className="block whitespace-nowrap text-signal">réservé</span>
            </h1>
            <p className="mt-8 max-w-xl font-mono text-[12.5px] uppercase leading-relaxed tracking-[0.04em] text-muted">
              Outil personnel de prospection locale. Saisis les identifiants de l&apos;application pour continuer.
            </p>
          </div>
          <ul className="hidden gap-[3px] font-mono text-[11px] uppercase tracking-[0.12em] sm:grid sm:grid-cols-3">
            {["Session 30 jours", "Cookie signé HttpOnly", "Aucun envoi auto"].map((t, i) => (
              <li key={t} className="flex min-h-[56px] items-center gap-2 border-2 border-ink px-3">
                <span className="text-accent">0{i + 1}</span> {t}
              </li>
            ))}
          </ul>
        </section>

        <section className="flex min-w-0 items-center bg-night px-4 py-10 text-night-text sm:px-6 md:px-10 md:py-14">
          <form action="/api/auth/login" method="post" className="w-full max-w-md lg:mx-auto">
            <input type="hidden" name="next" value={next} />
            <p className="mb-8 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-night-dim">
              <span className="text-accent-light">●</span> Identification
            </p>

            {error && (
              <div role="alert" className="mb-6 flex items-start gap-3 border-[3px] border-accent-light bg-night px-4 py-3 font-mono text-[13px] font-semibold uppercase leading-snug tracking-wide text-night-text">
                <span aria-hidden className="text-accent-light">✕</span>
                <span>{error}</span>
              </div>
            )}

            <label htmlFor="username" className="mb-2 block font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-night-text">
              Identifiant
            </label>
            <input id="username" name="username" type="text" autoComplete="username" autoCapitalize="none" spellCheck={false}
              required autoFocus aria-invalid={error ? true : undefined}
              className="login-field mb-6" />

            <label htmlFor="password" className="mb-2 block font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-night-text">
              Mot de passe
            </label>
            <input id="password" name="password" type="password" autoComplete="current-password" required
              aria-invalid={error ? true : undefined}
              className="login-field mb-8" />

            <button type="submit" className="login-submit">Se connecter</button>

          </form>
        </section>
      </main>
    </div>
  );
}
