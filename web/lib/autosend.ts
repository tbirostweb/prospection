/** Envoi automatique des e-mails (worker/local/outbox.py) : réglage, éligibilité et libellés. Désactivé par défaut. */

export type AutoSend = { enabled: boolean; daily_cap: number; start_hour: number; end_hour: number };
export const AUTO_DEFAULTS: AutoSend = { enabled: false, daily_cap: 10, start_hour: 9, end_hour: 17 };

/** Mêmes messageries gratuites que worker/local/contacts.py (FREEMAIL) : jamais d'envoi automatique vers elles. */
export const FREEMAIL = new Set(["gmail.com", "hotmail.com", "hotmail.fr", "outlook.com", "outlook.fr", "yahoo.fr", "yahoo.com", "orange.fr", "wanadoo.fr", "free.fr",
  "sfr.fr", "laposte.net", "live.fr", "icloud.com", "neuf.fr", "bbox.fr", "aol.com", "msn.com", "proton.me", "protonmail.com"]);

export const AUTO_STATES: Record<string, string> = {
  queued: "En file d'envoi", active: "Envoyé, relances prévues", replied: "A répondu", opted_out: "Ne plus contacter (réponse)",
  bounced: "Adresse invalide", abandoned: "Sans réponse (J+14)", stopped: "Retiré de l'envoi", failed: "Envoi refusé",
};

export const EMAIL_KINDS: Record<string, string> = { first: "Premier message", followup1: "Relance J+3", followup2: "Relance J+7" };

/** Raison pour laquelle ce prospect ne peut pas être mis en file (null = éligible). Le worker revérifie tout juste avant l'envoi. */
export function autoIneligible(p: { email?: string | null; email_kind?: string | null; do_not_contact?: any; contacted_at?: any; draft_message?: string | null }): string | null {
  const email = String(p.email ?? "").trim().toLowerCase();
  if (p.do_not_contact) return "entreprise en liste « ne plus contacter »";
  if (!/^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(email)) return "pas d'adresse e-mail";
  if (!["GENERIC_BUSINESS", "PERSONAL_BUSINESS"].includes(String(p.email_kind)) || FREEMAIL.has(email.split("@")[1]))
    return "adresse non professionnelle ou incertaine : à contacter toi-même";
  if (p.contacted_at) return "déjà contacté";
  if (!p.draft_message) return "pas de brouillon";
  return null;
}

export function sanitizeAutoSend(v: any): AutoSend | null {
  if (!v || typeof v !== "object") return null;
  const n = (x: any, lo: number, hi: number, d: number) => { const k = Math.round(Number(x)); return Number.isFinite(k) ? Math.min(hi, Math.max(lo, k)) : d; };
  const out = { enabled: v.enabled === true, daily_cap: n(v.daily_cap, 1, 30, 10), start_hour: n(v.start_hour, 6, 21, 9), end_hour: n(v.end_hour, 7, 22, 17) };
  return out.end_hour > out.start_hour ? out : null;
}
