export function ageLabel(dateStr: string | null): string {
  if (!dateStr) return "date inconnue";
  const diffMin = (Date.now() - new Date(dateStr).getTime()) / 60000;
  if (diffMin < 60) return `il y a ${Math.max(1, Math.round(diffMin))} min`;
  if (diffMin < 1440) return `il y a ${Math.round(diffMin / 60)} h`;
  return `il y a ${Math.round(diffMin / 1440)} j`;
}

export function dateFr(dateStr: string | null): string {
  return dateStr ? new Date(dateStr).toLocaleDateString("fr-FR", { timeZone: "UTC" }) : "—";
}

/** « 26/10 à 10:12 », heure de Paris (dates de la base en UTC). */
export function dateTimeFr(d: string | Date | null): string {
  if (!d) return "—";
  const x = new Date(d);
  return `${x.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit", timeZone: "Europe/Paris" })} à ${x.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Paris" })}`;
}
