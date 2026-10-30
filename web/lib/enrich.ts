/** Passages d'enrichissement des fiches existantes (worker/worker/local/enrich.py, table local_enrich_runs, migration 033) : ce que chaque
 *  sous-passage a traité et pourquoi il s'est arrêté, sans accès aux journaux du VPS. Mêmes clés que enrich.FREE_STEPS / ENGINE_STEPS. */

export type EnrichStep = { key: string; done: number; found: number; stop: string; detail?: string | null; ms?: number };
export type EnrichRun = { started_at: string | Date; duration_ms: number; mode: string; stopped: string | null; steps: EnrichStep[] };

/** Libellé de ce qu'un sous-passage a traité : [singulier, pluriel] (le nombre est mis devant). */
export const STEP_LABELS: Record<string, [string, string]> = {
  company_state: ["état officiel revérifié (SIRENE, BODACC)", "états officiels revérifiés (SIRENE, BODACC)"],
  naf: ["métier lu dans le code NAF", "métiers lus dans le code NAF"],
  manual: ["e-mail ajouté à la main rescoré", "e-mails ajoutés à la main rescorés"],
  stuck: ["fiche bloquée reprise", "fiches bloquées reprises"],
  site_identity: ["site retenu relu (identité)", "sites retenus relus (identité)"],
  places_site: ["site Google vérifié", "sites Google vérifiés"],
  places_backfill: ["fiche interrogée sur Google Places", "fiches interrogées sur Google Places"],
  site_guess: ["devinette de domaine", "devinettes de domaine"],
  email_domain_site: ["site du domaine de l'e-mail lu", "sites du domaine de l'e-mail lus"],
  unreachable: ["site injoignable réessayé", "sites injoignables réessayés"],
  site_email: ["site relu pour l'e-mail", "sites relus pour l'e-mail"],
  recheck: ["revérification de site", "revérifications de site"],
  search_more: ["recherche de site complétée", "recherches de site complétées"],
  stuck_search: ["fiche bloquée recherchée", "fiches bloquées recherchées"],
  email: ["recherche d'e-mail", "recherches d'e-mail"],
};
export const STOP_LABELS: Record<string, string> = {
  nothing: "rien à faire", limit: "plafond du passage", time: "budget de temps", quota: "quota", engines: "moteurs indisponibles", budget: "budget du jour réservé",
  lock: "verrou pris (campagne en cours)", error: "erreur", off: "désactivé",
};
// ce que compte `found` quand ce n'est pas « trouvé » : sites retirés par la vérification d'identité (worker/local/enrich.py site_identity)
const FOUND_LABELS: Record<string, [string, string]> = { site_identity: ["retiré", "retirés"] };
export const MODE_LABELS: Record<string, string> = { full: "complet", no_engines: "sans moteur", skipped: "pas lancé" };

/** « 10 sites Google vérifiés (2 trouvés), 4 revérifications de site, … » — seulement les sous-passages qui ont traité quelque chose. */
export function enrichSummary(steps: EnrichStep[]): string {
  const parts = steps.filter((s) => Number(s.done) > 0).map((s) => {
    const [one, many] = STEP_LABELS[s.key] ?? [s.key, s.key];
    const n = Number(s.done), f = Number(s.found ?? 0);
    const [fOne, fMany] = FOUND_LABELS[s.key] ?? ["trouvé", "trouvés"];
    return `${n} ${n > 1 ? many : one}${f > 0 ? ` (${f} ${f > 1 ? fMany : fOne})` : ""}`;
  });
  return parts.length ? parts.join(", ") : "aucune fiche traitée";
}

export const parseSteps = (v: any): EnrichStep[] => {
  if (typeof v === "string") { try { v = JSON.parse(v); } catch { return []; } }
  return Array.isArray(v) ? v : [];
};

/** Dernier passage (tous modes), dernier passage complet (avec moteurs), et bilan des 24 dernières heures. */
export const LAST_RUN_SQL = `SELECT started_at, duration_ms, mode, stopped, steps FROM local_enrich_runs ORDER BY id DESC LIMIT 1`;
export const LAST_FULL_RUN_SQL = `SELECT started_at, duration_ms, mode, stopped, steps FROM local_enrich_runs WHERE mode = 'full' ORDER BY id DESC LIMIT 1`;
export const DAY_RUNS_SQL = `SELECT COUNT(*) AS runs, COALESCE(SUM(mode = 'skipped'), 0) AS skipped,
    COALESCE(SUM(mode = 'full' AND stopped LIKE 'verrou pris%'), 0) AS locked
  FROM local_enrich_runs WHERE started_at >= UTC_TIMESTAMP() - INTERVAL 24 HOUR`;
