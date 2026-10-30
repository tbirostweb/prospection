/** Envoi automatique des e-mails (worker/local/outbox.py) : réglage, éligibilité et libellés. Désactivé par défaut. */
import { FicheFields, ficheReason } from "./complete";

/** personal_emails : envoyer aussi aux adresses Gmail, Orange… publiées comme contact de l'entreprise (activé par défaut, désactivable). */
export type AutoSend = { enabled: boolean; daily_cap: number; start_hour: number; end_hour: number; pause_start: number; pause_end: number; min_gap_min: number; personal_emails: boolean;
  /** warmup : montée en charge (3/jour la 1re semaine, 4 la 2e). bounce_ack_at : posé par la route des réglages à chaque enregistrement ; les e-mails revenus avant ne comptent plus pour le coupe-circuit. */
  warmup: boolean; bounce_ack_at?: string | null };
export const AUTO_DEFAULTS: AutoSend = { enabled: false, daily_cap: 10, start_hour: 10, end_hour: 16, pause_start: 12, pause_end: 13, min_gap_min: 60, personal_emails: true, warmup: true, bounce_ack_at: null };

/** Mêmes messageries gratuites que worker/local/contacts.py (FREEMAIL) : envoi automatique vers elles seulement avec personal_emails. */
export const FREEMAIL = new Set(["gmail.com", "hotmail.com", "hotmail.fr", "outlook.com", "outlook.fr", "yahoo.fr", "yahoo.com", "orange.fr", "wanadoo.fr", "free.fr",
  "sfr.fr", "laposte.net", "live.fr", "icloud.com", "neuf.fr", "bbox.fr", "aol.com", "msn.com", "proton.me", "protonmail.com"]);

export const AUTO_STATES: Record<string, string> = {
  queued: "En file d'envoi", active: "Envoyé, relances prévues", replied: "A répondu", opted_out: "Ne plus contacter (réponse)",
  bounced: "Adresse invalide", abandoned: "Sans réponse (J+14)", stopped: "Retiré de l'envoi", failed: "Envoi refusé",
};

export const EMAIL_KINDS: Record<string, string> = { first: "Premier message", followup1: "Relance J+3", followup2: "Relance J+7" };

/** Raison pour laquelle ce prospect ne peut pas être mis en file (null = éligible). Le worker revérifie tout juste avant l'envoi.
 *  `personal` (réglage personal_emails) : adresses Gmail, Orange… et adresses incertaines acceptées.
 *  E-mail d'abord (lib/complete.ts ficheReason) : métier connu, entreprise active et indépendante, adresse acceptée, QUEL QUE SOIT LE SITE ; le site
 *  oriente seulement le message (ficheAngle). Un brouillon dont l'angle ne correspond plus au site est effacé avant l'envoi par le worker. */
export function autoIneligible(p: FicheFields & { contacted_at?: any; draft_message?: string | null; draft_kind?: string | null }, personal: boolean): string | null {
  const email = String(p.email ?? "").trim().toLowerCase();
  if (p.do_not_contact) return "entreprise en liste « ne plus contacter »";
  if (!/^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(email)) return "pas d'adresse e-mail";
  if (personal) {
    if (!["GENERIC_BUSINESS", "PERSONAL_BUSINESS", "UNCERTAIN"].includes(String(p.email_kind))) return "adresse non vérifiée : à contacter toi-même";
  } else if (!["GENERIC_BUSINESS", "PERSONAL_BUSINESS"].includes(String(p.email_kind)) || FREEMAIL.has(email.split("@")[1]))
    return "adresse non professionnelle ou incertaine : à contacter toi-même";
  if (p.auto_state === "bounced") return "adresse invalide (e-mail revenu ou domaine sans messagerie) : par téléphone ou sur place";
  const incomplete = ficheReason(p, personal);
  if (incomplete) return incomplete;
  if (p.contacted_at) return "déjà contacté";
  if (!p.draft_message) return "pas de brouillon";
  // Brouillon de référence construit par le code (« Créer un brouillon ») : pas au style validé, jamais envoyé tel quel. Seul un message
  // rédigé avec /rediger (type « ai_… », ou « aib_… » pour la variante B du test, modifiable ensuite à la main) part automatiquement.
  if (p.draft_kind && !/^aib?_/.test(String(p.draft_kind))) return "brouillon de référence : à rédiger avec /rediger avant l'envoi automatique";
  return null;
}

export function sanitizeAutoSend(v: any): AutoSend | null {
  if (!v || typeof v !== "object") return null;
  const n = (x: any, lo: number, hi: number, d: number) => { const k = Math.round(Number(x)); return Number.isFinite(k) ? Math.min(hi, Math.max(lo, k)) : d; };
  const out = { enabled: v.enabled === true, personal_emails: v.personal_emails !== false, warmup: v.warmup !== false, daily_cap: n(v.daily_cap, 1, 30, 10), start_hour: n(v.start_hour, 6, 21, 10), end_hour: n(v.end_hour, 7, 22, 16),
    // pause déjeuner : le worker l'ignore si elle sort de la plage d'envoi ou si elle est vide (worker/local/mailer.py, _pause)
    pause_start: n(v.pause_start, 6, 21, 12), pause_end: n(v.pause_end, 7, 22, 13), min_gap_min: n(v.min_gap_min, 15, 240, 60) };
  return out.end_hour > out.start_hour ? out : null;
}

/** Mêmes paliers que worker/local/mailer.py (WARMUP_STEPS, effective_cap) : plafond du jour et fin du palier (null = plus de palier).
 *  `first` = date du plus ancien e-mail envoyé (aucun = jour 0). */
export function effectiveCap(cfg: { daily_cap: number; warmup?: boolean }, first: Date | null, now: Date = new Date()): { cap: number; until: Date | null } {
  if (cfg.warmup === false) return { cap: cfg.daily_cap, until: null };
  const days = first ? Math.max(0, Math.floor((now.getTime() - first.getTime()) / 86400000)) : 0;
  for (const [limit, cap] of [[7, 3], [14, 4]]) {
    if (days < limit) return { cap: Math.min(cfg.daily_cap, cap), until: new Date((first ?? now).getTime() + limit * 86400000) };
  }
  return { cap: cfg.daily_cap, until: null };
}
