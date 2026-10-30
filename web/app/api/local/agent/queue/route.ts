import { NextRequest, NextResponse } from "next/server";
import { shortActivity } from "@/lib/activity";
import { Offer, Sender, buildDraft, cap, isRedesignKind, kindAngle, photosOffered, salutation } from "@/lib/draft";
import { ANGLE_LABELS } from "@/lib/complete";
import { CONTACT_QUEUE_SQL, EMAIL_QUEUE_SQL, FORM_QUEUE_SQL, LIST_COLUMNS, getAutoSend, getSetting, safeRows } from "@/lib/localdb";
import { splitByTrigger, strongSignals } from "@/lib/local";
import { autoIneligible } from "@/lib/autosend";
import { safeHref } from "@/lib/security";

/** File de rédaction de la commande Claude Code /rediger (lecture seule ; authentification Basic APP_USER / APP_PASSWORD, comme tout script).
 *  Uniquement des FAITS sur l'entreprise (nom commercial, ville, activité, déclencheur, constats vérifiés) et le brouillon de référence construit
 *  par le code : ni le nom du gérant, ni e-mail, ni téléphone ne sortent d'ici (salutation, signature et notice sont ajoutées au dépôt).
 *  Prospects prêts de la file « À contacter », sans brouillon (sauf `?include_drafted=1`), déclencheurs forts d'abord.
 *  `?email_only=1` : seulement ceux qui peuvent partir par l'envoi automatique : e-mail utilisable (lib/complete.ts : métier connu, adresse
 *  acceptée par le réglage, entreprise active), jamais contactés, QUEL QUE SOIT LE SITE (file EMAIL_QUEUE_SQL : l'e-mail d'abord). Le site
 *  oriente seulement le message : `angle` refonte (site vérifié), creation (aucun site, recherche faite) ou neutre (site incertain : ne parler ni
 *  d'un site existant ni d'une absence de site).
 *  `?canal=formulaire` : fiches jamais contactées, sans e-mail utilisable, avec une page de contact à formulaire, triées par score (pas de
 *  déclencheurs d'abord) ; `canal: "formulaire"` et `page_contact` (URL) pour chaque prospect. Ces brouillons ne partent JAMAIS en envoi
 *  automatique : Théo les copie dans le formulaire (fiche → « Copier » / « Envoyé via le formulaire »). */
export async function GET(req: NextRequest) {
  const limit = Math.min(50, Math.max(1, Number(req.nextUrl.searchParams.get("limit")) || 5));
  const withDrafts = req.nextUrl.searchParams.get("include_drafted") === "1";
  const emailOnly = req.nextUrl.searchParams.get("email_only") === "1";
  const formOnly = req.nextUrl.searchParams.get("canal") === "formulaire";
  const sender = await getSetting<Sender>("local_sender", {});
  const offer = await getSetting<Offer>("local_offer", {});
  const auto = await getAutoSend();
  const rows = (await safeRows<any>(`SELECT ${LIST_COLUMNS}, p.website_evidence, p.contact_source_url, p.draft_message, p.provenance, p.auto_state ${formOnly ? FORM_QUEUE_SQL : emailOnly ? EMAIL_QUEUE_SQL : CONTACT_QUEUE_SQL}`))
    .filter((p) => withDrafts || !p.draft_message)
    .filter((p) => !emailOnly || autoIneligible({ ...p, draft_message: "-" }, auto.personal_emails) === null);
  const { triggered, others } = splitByTrigger(rows);
  const hasSite = isRedesignKind;
  const allDrafted = (formOnly ? rows : [...triggered, ...others]).map((p) => ({ p, d: buildDraft(p, sender, offer) }))
    .filter((x): x is { p: any; d: NonNullable<ReturnType<typeof buildDraft>> } => x.d !== null)   // aucun fait objectif : on n'écrit pas
    .map((x, i) => ({ ...x, i }));
  // Ceux qui peuvent partir par e-mail d'abord ; puis on ENTRELACE « avec site » (refonte) et « sans site » pour que les deux soient rédigés,
  // au lieu que l'un écrase l'autre (chaque groupe garde son ordre : déclencheurs forts, score).
  const canMail = (p: any) => autoIneligible({ ...p, draft_message: "-" }, auto.personal_emails) === null;
  const order = (list: typeof allDrafted) => {
    const w = list.filter((x) => hasSite(x.d.kind)), n = list.filter((x) => !hasSite(x.d.kind)), out: typeof allDrafted = [];
    for (let k = 0; k < Math.max(w.length, n.length); k++) { if (k < w.length) out.push(w[k]); if (k < n.length) out.push(n[k]); }
    return out;
  };
  const drafted = formOnly ? allDrafted : [...order(allDrafted.filter((x) => canMail(x.p))), ...order(allDrafted.filter((x) => !canMail(x.p)))];
  const items = [];
  for (const { p, d } of drafted) {
    if (items.length >= limit) break;
    const t = strongSignals(p)[0];
    items.push({
      id: p.id, entreprise: cap((p.trade_name || p.company_name || "").trim()),
      nom_commercial: p.trade_name ? cap(String(p.trade_name).trim()) : null,   // jamais la raison sociale : chez un entrepreneur individuel, elle contient son nom
      // formule d'appel imposée (ajoutée par le code au dépôt) : ne jamais écrire de salutation ni de nom dans le cœur du message
      salutation: salutation(p),
      ville: p.city ? cap(p.city) : null,
      activite: shortActivity(p.activity_label),   // forme courte : jamais un intitulé long dans la phrase
      declencheur: t ? { libelle: t.label, detail: t.detail ?? null } : null,
      faits: d.facts, type: d.kind,
      // angle imposé par l'état du site : refonte, creation ou neutre (neutre : ni « votre site » ni « pas de site », refusé au dépôt sinon)
      angle: kindAngle(d.kind), angle_libelle: ANGLE_LABELS[kindAngle(d.kind)],
      // jamais de prix ni de lien dans le message (création, refonte ou neutre) : le site est dans la signature, ajoutée par l'application
      offre_prospect: { type: kindAngle(d.kind), prix: null, lien_offres: null,
        regle: "AUCUN prix, aucun montant, aucune réduction, AUCUN lien : terminer par « Je vous partage mon site pour plus d'informations. » (le site est dans la signature)" },
      photos_en_option: photosOffered(p, offer), canal: formOnly ? "formulaire" : p.email && p.auto_state !== "bounced" ? "e-mail" : p.contact_form ? "formulaire" : "téléphone ou visite",
      page_contact: formOnly ? safeHref(p.contact_page) : undefined,
      // envoi automatique possible (fiche complète avec e-mail, jamais contactée) ; sinon Théo contacte lui-même (jamais sans métier connu)
      envoi_auto: !formOnly && autoIneligible({ ...p, draft_message: "-" }, auto.personal_emails) === null,
      reference: { objet: d.subject, coeur: d.core }, fiche: `/local/${p.id}`,
    });
  }
  return NextResponse.json({
    // offre générale, SANS prix (jamais de prix dans un message) ; pour rédiger, n'utiliser que `offre_prospect` de chaque prospect
    offre: { active: !!offer.enabled, zone: offer.area ?? "", lien_offres: null, photos_jusqu_a_km: offer.photos_max_km ?? 20 },
    expediteur: { activite: sender.role || "développeur web indépendant", marque: sender.company || null },
    prospects: items,
  });
}
