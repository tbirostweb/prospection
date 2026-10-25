import type { Metadata } from "next";
import { safeNextPath } from "@/lib/security";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Connexion · Prospection locale", robots: { index: false, follow: false } };

const ERRORS: Record<string, string> = {
  "1": "Identifiant ou mot de passe incorrect.",
  blocked: "Trop de tentatives. Réessaie dans quelques minutes.",
};

/** Page de connexion (carte centrée) (remplace la boîte HTTP Basic du navigateur). Formulaire HTML classique : fonctionne sans JavaScript. */
export default async function LoginPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);
  const next = safeNextPath(one(sp.next));
  const error = ERRORS[one(sp.error) ?? ""];

  return (
    <main className="flex min-h-screen min-h-[100dvh] items-center justify-center bg-canvas px-4 py-10 text-ink">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2.5">
          <span className="grid h-10 w-10 place-items-center rounded-full bg-signal text-base font-bold text-white" aria-hidden>P</span>
          <span className="text-lg font-semibold tracking-[-0.01em]">Prospection</span>
        </div>
        <form action="/api/auth/login" method="post" className="card !p-6 sm:!p-8">
          <input type="hidden" name="next" value={next} />
          <h1 className="text-2xl font-semibold tracking-[-0.02em]">Connexion</h1>
          <p className="mt-1 text-sm text-muted">Outil personnel : saisis les identifiants de l&apos;application.</p>

          {error && (
            <div role="alert" className="mt-5 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-medium text-red-800">{error}</div>
          )}

          <label htmlFor="username" className="mb-1.5 mt-6 block text-sm font-medium">Identifiant</label>
          <input id="username" name="username" type="text" autoComplete="username" autoCapitalize="none" spellCheck={false}
            required autoFocus aria-invalid={error ? true : undefined} className="login-field" />

          <label htmlFor="password" className="mb-1.5 mt-4 block text-sm font-medium">Mot de passe</label>
          <input id="password" name="password" type="password" autoComplete="current-password" required
            aria-invalid={error ? true : undefined} className="login-field" />

          <button type="submit" className="login-submit mt-6">Se connecter</button>
        </form>
        <p className="mt-4 text-center text-xs text-muted">Session de 30 jours · aucun e-mail envoyé automatiquement</p>
      </div>
    </main>
  );
}
