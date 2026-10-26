import { NextRequest, NextResponse } from "next/server";
import { Offer, Sender, buildDraft, cap, photosOffered } from "@/lib/draft";
import { CONTACT_QUEUE_SQL, LIST_COLUMNS, getAutoSend, getSetting, safeRows } from "@/lib/localdb";
import { splitByTrigger, strongSignals } from "@/lib/local";
import { autoIneligible } from "@/lib/autosend";

/** File de rédaction de la commande Claude Code /rediger (lecture seule ; authentification Basic APP_USER / APP_PASSWORD, comme tout script).
 *  Uniquement des FAITS sur l'entreprise (nom commercial, ville, activité, déclencheur, constats vérifiés) et le brouillon de référence construit
 *  par le code : ni le nom du gérant, ni e-mail, ni téléphone ne sortent d'ici (salutation, signature et notice sont ajoutées au dépôt).
 *  Prospects prêts de la file « À contacter », sans brouillon (sauf `?include_drafted=1`), déclencheurs forts d'abord. */
export async function GET(req: NextRequest) {
  const limit = Math.min(50, Math.max(1, Number(req.nextUrl.searchParams.get("limit")) || 5));
  const withDrafts = req.nextUrl.searchParams.get("include_drafted") === "1";
  const sender = await getSetting<Sender>("local_sender", {});
  const offer = await getSetting<Offer>("local_offer", {});
  const auto = await getAutoSend();
  const rows = (await safeRows<any>(`SELECT ${LIST_COLUMNS}, p.website_evidence, p.contact_source_url, p.draft_message ${CONTACT_QUEUE_SQL}`))
    .filter((p) => withDrafts || !p.draft_message);
  const { triggered, others } = splitByTrigger(rows);
  const items = [];
  for (const p of [...triggered, ...others]) {
    if (items.length >= limit) break;
    const d = buildDraft(p, sender, offer);
    if (!d) continue;                                   // aucun fait objectif : on n'écrit pas
    const t = strongSignals(p)[0];
    items.push({
      id: p.id, entreprise: cap((p.trade_name || p.company_name || "").trim()),
      nom_commercial: p.trade_name ? cap(String(p.trade_name).trim()) : null,   // jamais la raison sociale : chez un entrepreneur individuel, elle contient son nom
      ville: p.city ? cap(p.city) : null,
      activite: p.activity_label ? String(p.activity_label).split(",")[0] : null,
      declencheur: t ? { libelle: t.label, detail: t.detail ?? null } : null,
      faits: d.facts, type: d.kind, photos_en_option: photosOffered(p, offer), canal: p.email ? "e-mail" : p.contact_form ? "formulaire" : "téléphone ou visite",
      // envoi automatique possible (adresse e-mail acceptée par le réglage, jamais contacté) ; sinon Théo contacte lui-même
      envoi_auto: autoIneligible({ ...p, draft_message: "-" }, auto.personal_emails) === null,
      reference: { objet: d.subject, coeur: d.core }, fiche: `/local/${p.id}`,
    });
  }
  return NextResponse.json({
    offre: { active: !!offer.enabled, zone: offer.area ?? "", prix_normal: offer.normal_price ?? null, prix_lancement: offer.launch_price ?? null,
      places_lancement_restantes: offer.launch_slots_left ?? 0, photos_jusqu_a_km: offer.photos_max_km ?? 20 },
    expediteur: { activite: sender.role || "développeur web indépendant", marque: sender.company || null },
    prospects: items,
  });
}
