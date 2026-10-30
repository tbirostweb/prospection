import { draftAngle } from "./complete";

/** Tableau « Ce qui répond » : pour les fiches contactées, par métier, par angle du message et par tranche de score. */
export const MIN_CONTACTED = 3;
export type ReplyInput = { activity_label?: string | null; draft_kind?: string | null; prospect_score?: number | string | null; status?: string | null;
  response_status?: string | null; reply_kind?: string | null };
export type ReplyRow = { name: string; contacted: number; replied: number; interested: number; rate: number | null };

/** A répondu : réponse classée par le worker, statut de réponse, ou réponse notée à la main (« pas intéressé » compte : c'est une réponse). */
export const hasReplied = (r: ReplyInput) => !!r.reply_kind || ["REPLIED", "INTERESTED", "WON"].includes(r.status ?? "")
  || ["REPLIED", "INTERESTED", "NOT_INTERESTED", "WON"].includes(r.response_status ?? "");
export const isInterested = (r: ReplyInput) => r.reply_kind === "interested" || ["INTERESTED", "WON"].includes(r.status ?? "")
  || ["INTERESTED", "WON"].includes(r.response_status ?? "");

export const ANGLE_LABELS: Record<string, string> = { refonte: "Refonte (site à améliorer)", creation: "Création (pas de site)", neutre: "Neutre" };
export function scoreBand(score: number | string | null | undefined): string {
  const n = Number(score);
  if (score == null || score === "" || !Number.isFinite(n)) return "Sans score";
  return n >= 80 ? "80 et plus" : n >= 60 ? "60 à 79" : n >= 40 ? "40 à 59" : "Moins de 40";
}

/** Regroupe les lignes par `key`, les groupes de moins de `min` contactés sont fondus dans « Autres ». Tri : plus de contactés d'abord (ordre `order` si fourni). */
export function groupReplies(rows: ReplyInput[], key: (r: ReplyInput) => string, min = MIN_CONTACTED, order?: string[]): ReplyRow[] {
  const acc = new Map<string, { contacted: number; replied: number; interested: number }>();
  for (const r of rows) {
    const g = acc.get(key(r)) ?? { contacted: 0, replied: 0, interested: 0 };
    g.contacted++; if (hasReplied(r)) g.replied++; if (isInterested(r)) g.interested++;
    acc.set(key(r), g);
  }
  const out: ReplyRow[] = [], rest = { contacted: 0, replied: 0, interested: 0 };
  for (const [name, g] of acc) {
    if (g.contacted >= min) out.push({ name, ...g, rate: g.replied / g.contacted });
    else { rest.contacted += g.contacted; rest.replied += g.replied; rest.interested += g.interested; }
  }
  const rank = (n: string) => { const i = order?.indexOf(n) ?? -1; return i < 0 ? 99 : i; };
  out.sort((a, b) => rank(a.name) - rank(b.name) || b.contacted - a.contacted || a.name.localeCompare(b.name, "fr"));
  if (rest.contacted > 0) out.push({ name: `Autres (moins de ${min} contactés par ligne)`, ...rest, rate: rest.replied / rest.contacted });
  return out;
}

export function replyTables(rows: ReplyInput[]) {
  return {
    activity: groupReplies(rows, (r) => (r.activity_label || "Non classée").trim()),
    angle: groupReplies(rows, (r) => { const a = draftAngle(r.draft_kind); return a ? ANGLE_LABELS[a] : "Message écrit à la main / inconnu"; }, MIN_CONTACTED,
      Object.values(ANGLE_LABELS)),
    score: groupReplies(rows, (r) => scoreBand(r.prospect_score), MIN_CONTACTED, ["80 et plus", "60 à 79", "40 à 59", "Moins de 40", "Sans score"]),
  };
}
