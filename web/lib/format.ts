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
