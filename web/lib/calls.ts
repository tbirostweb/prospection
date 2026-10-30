import { shortActivity } from "./activity";
import type { Angle } from "./complete";

/** « À appeler » et formulaire : fiches SANS e-mail utilisable, contactées à la main par Théo. Rien n'est envoyé automatiquement. */
export const CALL_MAX_ATTEMPTS = 3;          // au 3e appel sans réponse, la fiche sort de la liste
export const NO_ANSWER_RETRY_DAYS = 2;       // « Pas de réponse » : rappel J+2
export const FORM_FOLLOWUP_DAYS = 7;         // formulaire envoyé : relance J+7

/** Jour AAAA-MM-JJ + n jours (calendrier, sans fuseau). */
export function addDays(day: string, n: number): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Argumentaire d'appel en 3 lignes, construit à partir du métier et de l'angle. Constat prudent, aucun prix. */
export function callScript(activityLabel: string | null | undefined, angle: Angle): string[] {
  const metier = shortActivity(activityLabel)?.toLowerCase() ?? null;
  const intro = "Bonjour, Théo, développeur web indépendant près de Troyes. Je vous dérange une minute ?";
  const constat = angle === "refonte"
    ? "En regardant votre site, j'ai vu quelques points qui pourraient être améliorés (affichage, rapidité, lisibilité sur mobile)."
    : angle === "creation"
      ? `Je n'ai pas trouvé de site internet pour votre activité${metier ? ` de ${metier}` : ""}, sauf erreur de ma part.`
      : `J'aide des entreprises de la région${metier ? ` (${metier})` : ""} à mieux se faire trouver sur internet.`;
  return [intro, constat, "Est-ce que ce serait un sujet pour vous ?"];
}

export type CallResult = "no_answer" | "callback" | "interested" | "not_interested";
export const CALL_RESULTS: CallResult[] = ["no_answer", "callback", "interested", "not_interested"];
/** Ce qu'un résultat d'appel change sur la fiche. `reached` : quelqu'un a répondu (fiche marquée contactée par téléphone). `remind` : undefined = inchangé, null = supprimé. */
export type CallPlan = { reached: boolean; attemptsInc: boolean; status?: string; response?: string; dealStage?: string;
  remind?: { at: string; note: string } | null };

/** `attempts` : appels déjà restés sans réponse (avant celui-ci). `date` : jour demandé pour « Rappeler ». null si le résultat ou la date est invalide. */
export function planCallResult(result: string, today: string, attempts: number, date?: string | null): CallPlan | null {
  if (result === "no_answer") {
    // 3e appel sans réponse : plus de rappel, la fiche sort de la liste
    return attempts + 1 >= CALL_MAX_ATTEMPTS ? { reached: false, attemptsInc: true, remind: null }
      : { reached: false, attemptsInc: true, remind: { at: addDays(today, NO_ANSWER_RETRY_DAYS), note: `Rappeler (appel ${attempts + 1} sans réponse)` } };
  }
  if (result === "callback") {
    if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(new Date(`${date}T00:00:00Z`).getTime()) || addDays(date, 0) !== date) return null;
    return { reached: true, attemptsInc: false, status: "CONTACTED", remind: { at: date, note: "Rappeler (demandé au téléphone)" } };
  }
  if (result === "interested") return { reached: true, attemptsInc: false, status: "INTERESTED", response: "INTERESTED", dealStage: "REPLIED",
    remind: { at: today, note: "Intéressé au téléphone : proposer un échange plus long" } };
  if (result === "not_interested") return { reached: true, attemptsInc: false, status: "LOST", response: "NOT_INTERESTED", dealStage: "LOST", remind: null };
  return null;
}
