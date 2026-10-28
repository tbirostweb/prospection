// Brouillon d'e-mail PERSONNALISÉ, créé seulement quand tu cliques « Créer un brouillon » — envoyé seulement s'il est mis en file ET que l'envoi automatique est activé (worker/local/outbox.py).
// Construit UNIQUEMENT avec des faits réellement détectés sur l'entreprise (site non trouvé, problèmes objectifs du site, création récente…).
// Ton : court, courtois, sans fausse affirmation (« sauf erreur de ma part »), toujours avec une ligne permettant de refuser tout nouveau contact.

import { siteSearched, verifiedSocials } from "./local";

export interface Sender { name?: string; company?: string; role?: string; phone?: string; email?: string; website?: string; pitch?: string; privacy_url?: string }

/** Marqueurs laissés dans un brouillon quand une information OBLIGATOIRE manque : tant qu'ils sont présents, l'envoi (mailto) est désactivé. */
export const MISSING_NOTICE = "[LIEN VERS VOTRE NOTICE D'INFORMATION À RENSEIGNER DANS RÉGLAGES]";
export const MISSING_NAME = "[Votre prénom et nom]";
export const draftIncomplete = (text: string | null | undefined) => !!text && (text.includes(MISSING_NOTICE) || text.includes(MISSING_NAME));

/** Information des personnes dont les coordonnées n'ont pas été collectées auprès d'elles (art. 14 RGPD) et moyen simple de refuser les
 *  messages suivants (art. L34-5 CPCE) : UNE ligne discrète, sous la signature, avec la source et le lien vers la notice détaillée. */
export function informationNotice(p: any, sender: Sender = {}): string {
  let source = "";
  try { source = p.contact_source_url ? new URL(p.contact_source_url).hostname.replace(/^www\./, "") : ""; } catch { /* URL illisible */ }
  const origin = source ? `(notamment ${source})` : "(annuaire officiel des entreprises, site internet)";
  const url = sender.privacy_url?.trim() || MISSING_NOTICE;
  return `Coordonnées trouvées sur des sources publiques ${origin}. Si vous ne souhaitez plus de message de ma part, dites-le-moi simplement. En savoir plus : ${url}`;
}
export interface Draft { subject: string; body: string; core: string; facts: string[]; kind: string }

/** Offre « clé en main » (Réglages → « Mon offre ») : le texte est fixe, seuls les chiffres viennent des réglages, jamais du code (dépôt public).
 *  Prix absents = aucune phrase de prix ; places de lancement à 0 = pas de tarif de lancement. Le paiement, le domaine et l'hébergement se
 *  règlent au devis : jamais dans le premier message. */
export interface Offer { enabled?: boolean; area?: string; launch_price?: number | null; normal_price?: number | null; launch_slots_left?: number | null;
  photos_max_km?: number | null;
  /** Refonte (site existant) : AUCUN prix dans le message (il dépend de l'état du site) ; renvoi vers cette page d'offres (http/https). Vide = site de la signature. */
  offers_url?: string | null }
/** Messages concernant un site existant (refonte) : l'offre de création (prix de lancement, prix barré) n'y a pas sa place. */
export const isRedesignKind = (kind: string) => /site_ameliorable|site_refonte|site_inaccessible/.test(kind);
const clampNum = (v: unknown, lo: number, hi: number): number | null => { const n = Number(v); return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : null; };
/** Valide le réglage « Mon offre » (null = refus). */
export function sanitizeOffer(value: any): Offer | null {
  if (!value || typeof value !== "object") return null;
  const price = (k: string) => (value[k] === null || value[k] === "" || value[k] === undefined ? null : clampNum(value[k], 0, 100000));
  const slots = clampNum(value.launch_slots_left ?? 0, 0, 20);
  const url = String(value.offers_url ?? "").trim().slice(0, 300);
  if (url && !/^https?:\/\/[^\s"<>]+\.[^\s"<>]+$/i.test(url)) return null;
  return { enabled: !!value.enabled, area: String(value.area ?? "").trim().slice(0, 80), launch_price: price("launch_price"),
    normal_price: price("normal_price"), offers_url: url || null, launch_slots_left: slots === null ? 0 : Math.floor(slots),
    photos_max_km: clampNum(value.photos_max_km ?? 20, 0, 100) ?? 20 };
}
/** Lien « mes offres » d'un message de refonte : le réglage, sinon le site de la signature, sinon rien. */
export const offersLink = (offer: Offer, sender: { website?: string }) => offer.offers_url?.trim() || sender.website?.trim().replace(/\/$/, "") || "";
const euros = (n: number) => `${new Intl.NumberFormat("fr-FR").format(n).replace(/\u202f|\u00a0/g, " ")} €`;

/** Métiers où des photos faites sur place servent vraiment le site (salon, atelier, chantier, vitrine…). Jamais les photographes. */
export const PHOTO_ACTIVITIES = new Set(["coiffure", "beaute", "soins_personnels", "fitness", "restaurants", "bars", "traiteurs", "boulangeries", "garages",
  "commerces", "boutiques", "fleuristes", "opticiens", "artisans_art", "menuisiers", "paysagistes", "peintres", "couvreurs", "macons", "plombiers",
  "electriciens", "hebergements", "immobilier", "evenementiel", "auto_ecoles"]);
export const PHOTOS_MAX_KM = 20;
/** Proposer les photos en option : métier concerné ET établissement assez proche (distance au centre de la campagne, ≤ `photos_max_km`). */
export function photosOffered(p: { activity_key?: string | null; distance_km?: any }, offer: Offer = {}): boolean {
  const km = p.distance_km === null || p.distance_km === undefined || p.distance_km === "" ? NaN : Number(p.distance_km);
  const max = Number(offer.photos_max_km ?? PHOTOS_MAX_KM);
  return PHOTO_ACTIVITIES.has(p.activity_key ?? "") && Number.isFinite(km) && km <= max;
}

export function offerParagraphs(offer: Offer, opts: { photos?: boolean; redesign?: boolean; link?: string } = {}): string[] {
  const out = [
    "Concrètement, j'écris les textes à partir de ce que vous me racontez de votre métier, vous n'avez qu'à relire. "
      + "Le site est en ligne en deux à quatre semaines. "   // pas de bilan à trois mois tant que Théo ne l'a pas confirmé
      + (opts.photos ? "En option, je peux aussi créer ou compléter votre fiche Google et passer faire les photos." : "En option, je peux aussi créer ou compléter votre fiche Google."),
  ];
  if (opts.redesign) {   // refonte : aucun prix (il dépend de l'état du site), seulement un renvoi vers les offres
    if (opts.link) out.push(`Mes offres et tarifs sont détaillés sur ${opts.link}.`);
    return out;
  }
  const slots = Math.max(0, Math.floor(Number(offer.launch_slots_left) || 0));
  const launch = Number(offer.launch_price) || 0, normal = Number(offer.normal_price) || 0;
  if (slots > 0 && launch > 0 && normal > launch) {
    out.push(`C'est mon offre de lancement : pour ${slots > 1 ? `les ${slots} prochaines entreprises` : "la prochaine entreprise"}, `
      + `le site est à ${euros(launch)} au lieu de ${euros(normal)}.`);   // jamais de réduction contre un avis : interdit par Google (avis incités)
  } else if (normal > 0) {
    out.push(`Le site est à ${euros(normal)}.`);
  }
  return out;
}

/** Une seule demande à la fin : le site (réalisations, tarifs, devis, réservation d'un appel ou d'une visio) ; sans site réglé, une réponse au message. */
export function callToAction(website?: string): string {
  const site = website?.trim().replace(/\/$/, "");
  return site
    ? `Vous pouvez voir mes réalisations et mes tarifs sur ${site}, et y demander un devis ou réserver un appel ou une visio.`
    : "Si vous voulez en parler, répondez simplement à ce message.";
}

export const ISSUE_PHRASES: Record<string, string> = {
  noindex: "la page d'accueil semble demander aux moteurs de recherche de ne pas l'indexer",
  robots_blocks_all: "le fichier robots.txt semble interdire tout le site aux moteurs de recherche",
  no_https: "le site n'est pas servi en HTTPS (les navigateurs l'affichent comme « non sécurisé »)",
  no_viewport: "l'affichage sur téléphone ne semble pas adapté",
  no_https_redirect: "l'adresse en HTTP ne redirige pas vers la version sécurisée",
  slow_response: "la page d'accueil met plusieurs secondes à répondre",
  old_tech: "certaines technologies du site semblent anciennes",
  title_missing: "la page d'accueil n'a pas de titre pour Google",
  meta_description_missing: "la page d'accueil n'a pas de description pour Google (le texte affiché sous le lien dans les résultats)",
  h1_missing: "la page d'accueil n'a pas de titre principal",
  title_generic: "le titre de la page d'accueil est très générique",
  sitemap_missing: "je n'ai pas trouvé de plan du site (sitemap) pour les moteurs de recherche",
  heavy_page: "la page d'accueil est très lourde à charger",
  free_subdomain: "le site est hébergé sur une adresse gratuite (sous-domaine d'un créateur de sites), moins crédible et moins bien référencée",
  directory_site: "le site semble fourni par un annuaire, avec une présentation assez générique",
  site_builder: "le site semble fait avec un créateur de sites en ligne, souvent limité pour être bien trouvé sur Google",
  structured_data_missing: "la page d'accueil ne donne pas à Google vos informations pratiques (adresse, horaires) sous une forme qu'il sait lire",
  js_shell: "le contenu de la page d'accueil ne s'affiche qu'avec JavaScript, ce que les moteurs de recherche lisent mal",
};
// Site daté : la mention « © » la plus récente du site a au moins OLD_SITE_YEARS ans (fait vérifiable, l'année est citée telle quelle).
const OLD_SITE_YEARS = 6;
// Événements officiels (BODACC) qui donnent une raison d'écrire MAINTENANT — citées seulement si publiées il y a moins d'un an.
export const EVENT_OPENINGS: Record<string, (name: string, where: string) => string> = {
  takeover: (name, where) => `J'ai vu que vous aviez repris ${name}${where} récemment : félicitations !`,
  rename: (name) => `J'ai vu que votre entreprise porte désormais le nom ${name} : félicitations pour ce changement !`,
  move: (name, where) => `J'ai vu que ${name} s'est récemment installé${where} : bonne continuation dans vos nouveaux locaux !`,
};
export const PRIORITY = Object.keys(ISSUE_PHRASES);
const j = (v: any) => (typeof v === "string" ? (() => { try { return JSON.parse(v); } catch { return null; } })() : v);
const SMALL = new Set(["de", "du", "des", "la", "le", "les", "et", "à", "au", "aux", "en", "sur", "d", "l"]);
/** « CAFE DE LA GARE » → « Cafe de la Gare » (articles en minuscules, sauf en tête). */
export const cap = (s: string) => s.toLowerCase().split(/(\s+|-|'|’)/).map((w, i) => (i > 0 && SMALL.has(w) ? w : w.charAt(0).toUpperCase() + w.slice(1))).join("");


/** `p` : ligne `local_prospects`. Renvoie null quand AUCUN fait objectif ne justifie un message (on n'invente jamais une faiblesse). */
/** « de Le Petit Bistrot » → « du Petit Bistrot », « de Les Délices » → « des Délices », « de L'Atelier » → « de l'Atelier », « de Atelier Exemple » → « d'Atelier Exemple ». */
export const deName = (name: string) => /^le\s/i.test(name) ? `du ${name.slice(3)}` : /^les\s/i.test(name) ? `des ${name.slice(4)}` : /^la\s/i.test(name) ? `de la ${name.slice(3)}` : /^l['’]/i.test(name) ? `de l'${name.slice(2)}` : /^[aeiouyàâéèêëîïôöûüœæ]/i.test(name) ? `d'${name}` : `de ${name}`;

export function buildDraft(p: any, sender: Sender = {}, offer: Offer = {}): Draft | null {
  const name = cap((p.trade_name || p.company_name || "").trim());
  const city = p.city ? cap(p.city) : null;
  const where = city ? ` à ${city}` : "";
  const issues: any[] = j(p.issues) ?? [];
  const ev = j(p.website_evidence) ?? {};
  const socials: string[] = verifiedSocials(p).map((x) => x.url);                // vérifiés seulement : jamais un profil voisin
  const status = p.website_status;
  const facts: string[] = [];
  let opening = "", kind = "";
  const sigs: any[] = j(p.buy_signals) ?? [];
  const event = sigs.find((x) => EVENT_OPENINGS[x.code]);
  const ref = event ? "votre entreprise" : `${name}${where}`;                    // on vient de nommer l'entreprise dans l'ouverture : pas de répétition

  if (status === "NOT_FOUND" && !siteSearched(p)) {
    return null;                                                                   // recherche trop maigre : « pas de site » n'est pas encore un fait
  } else if (status === "NOT_FOUND") {
    kind = "sans_site";
    const onSocial = socials.length ? ` Je vois que vous êtes présent${socials.some((u) => /facebook/.test(u)) ? " sur Facebook" : " sur les réseaux sociaux"}, mais` : " En faisant quelques recherches,";
    opening = `${onSocial} je n'ai pas trouvé de site internet pour ${ref}, sauf erreur de ma part.`;
    facts.push("aucun site internet officiel trouvé lors de mes recherches");
  } else if (status === "UNREACHABLE") {
    kind = "site_inaccessible";
    opening = ` En voulant consulter le site de ${name}, je n'ai pas pu y accéder lors de ma vérification${p.website_url ? ` (${p.website_url})` : ""}.`;
    facts.push("site inaccessible lors de ma vérification");
  } else if (status === "CONFIRMED" || status === "PROBABLE") {
    const codes = new Set(issues.map((i) => i.code));
    const found = PRIORITY.filter((c) => codes.has(c)).map((c) => ISSUE_PHRASES[c]);
    const year = Number(p.site_year);
    if (year && year <= new Date().getFullYear() - OLD_SITE_YEARS) found.unshift(`la mention de copyright du site date de ${year}, il a sans doute vieilli`);
    const addr = p.website_url ? String(p.website_url).replace(/^https?:\/\//, "").replace(/\/$/, "") : "";
    if (found.length) {
      kind = "site_ameliorable";
      facts.push(...found.slice(0, 3));
      opening = ` J'ai regardé le site de ${name}${addr ? ` (${addr})` : ""}, seulement ce qui est visible publiquement, et j'ai relevé quelques points :`;
    } else {
      // Aucun défaut vérifié : on le dit honnêtement (« j'ai regardé »), on n'invente aucune faiblesse et on propose seulement un regard neuf.
      // Site récent (© d'il y a moins de 2 ans) : pas de refonte à proposer (même seuil que worker/local/scoring.py RECENT_SITE_YEAR).
      if (!addr || (year && year >= new Date().getFullYear() - 2)) return null;
      kind = "site_refonte";
      facts.push(`site regardé (${addr}), aucun défaut précis relevé : ne pas en citer`);
      opening = ` J'ai regardé le site de ${name} (${addr}), seulement ce qui est visible publiquement.`;
    }
  } else {
    return null;
  }

  const first = event ? `${EVENT_OPENINGS[event.code](name, where)}${opening}`
    : opening.trim();   // jamais de « félicitations pour le lancement » d'après la seule date d'immatriculation
  const who = [sender.name, sender.role ? sender.role : "développeur web", sender.company ? `(${sender.company})` : ""].filter(Boolean).join(", ").replace(", (", " (");
  const pitch = sender.pitch?.trim()
    || (kind === "sans_site"
      ? `Je crée des sites simples et efficaces pour les indépendants et les petites entreprises de la région : être trouvé sur Google, présenter vos services et être joignable facilement depuis un téléphone.`   // jamais de spécialisation inventée par métier
      : kind === "site_refonte"
        ? "Je n'ai rien relevé à lui reprocher. Je propose simplement un regard neuf : un site plus moderne, que vous pouvez mettre à jour plus facilement, qui facilite les demandes de devis et met mieux en valeur vos réalisations."
      : kind === "site_inaccessible"
        ? "Je peux vous aider à le remettre en ligne rapidement, et si besoin à le moderniser pour qu'il soit trouvé et consultable sur téléphone."
        : "Je peux vous proposer une refonte : un nouveau site, plus actuel, agréable à consulter sur téléphone et pensé pour qu'on vous trouve sur Google, dont j'écris les textes avec vous.");   // une refonte, jamais « corriger » le site actuel
  const list = kind === "site_ameliorable" ? "\n" + facts.map((f) => `- ${f}`).join("\n") : "";
  const area = offer.enabled && offer.area?.trim() ? `, ${offer.area.trim()}` : "";
  const question = kind === "site_refonte" ? "Est-ce un sujet pour vous en ce moment, ou votre site actuel vous convient-il tel qu'il est ?\n\n" : "";
  const ask = question + (offer.enabled
    ? `${offerParagraphs(offer, { photos: photosOffered(p, offer), redesign: isRedesignKind(kind), link: offersLink(offer, sender) }).join("\n\n")}\n\n${callToAction(isRedesignKind(kind) ? "" : sender.website)}`
    : "Seriez-vous d'accord pour un échange de 10 minutes, sans engagement ? Je peux aussi vous envoyer quelques exemples de réalisations.");
  const core = `${first}${list}\n\nJe suis ${who || "développeur web indépendant"}${area}. ${pitch}\n\n${ask}`;
  const body = wrapDraft(p, sender, core);
  const subject = kind === "sans_site" ? `${name} : votre présence en ligne${city ? ` à ${city}` : ""}`
    : kind === "site_inaccessible" ? `${name} : votre site semble inaccessible`
    : kind === "site_refonte" ? `${name} : un regard neuf sur votre site ?`
    : `${name} : quelques remarques sur votre site`;
  return { subject, body, core, facts, kind: event ? `${event.code}_${kind}` : kind };  // type mesuré par l'apprentissage (quel message obtient des réponses)
}

/** Garde-fous d'un cœur de message rédigé HORS de l'application (/rediger) : on ne fait confiance qu'aux faits du brouillon de référence.
 *  Tout nombre ou lien absent de la référence est refusé (un chiffre inventé est la pire erreur d'un message de prospection). [] = accepté. */
export function checkWrittenCore(core: string, subject: string, ref: Draft): string[] {
  const problems: string[] = [];
  const text = `${core}\n${subject}`;
  if (core.trim().length < 150) problems.push("message trop court (150 caractères au moins)");
  if (core.length > 2500) problems.push("message trop long (2 500 caractères au plus)");
  if (!subject.trim() || subject.length > 120) problems.push("objet vide ou trop long (120 caractères au plus)");
  const known = `${ref.core}\n${ref.subject}\n${ref.facts.join("\n")}`;
  const allowedNumbers = new Set(known.match(/\d+(?:[ ,.]\d+)*/g) ?? []);
  const numbers = [...new Set(text.match(/\d+(?:[ ,.]\d+)*/g) ?? [])].filter((n) => !allowedNumbers.has(n));
  if (numbers.length) problems.push(`chiffres absents des faits et de l'offre : ${numbers.join(", ")}`);
  if (isRedesignKind(ref.kind) && (/€|\beuros?\b/i.test(text) || /au lieu de|offre de lancement|prochaines? entreprises?|réduction|remise|promo|prix barré|à partir de \d/i.test(text)))
    problems.push("message de site existant : aucun prix ni réduction (renvoyer vers la page d'offres du brouillon de référence)");
  const links = [...new Set(text.match(/https?:\/\/\S+|www\.\S+/gi) ?? [])].filter((u) => !known.includes(u.replace(/[).,;:!?]+$/, "")));
  if (links.length) problems.push(`liens absents des faits : ${links.join(", ")}`);
  if (/\[[^\]]*\]/.test(text)) problems.push("mention entre crochets à remplacer");
  if (/garanti/i.test(text)) problems.push("aucune garantie de résultat ne peut être promise");
  if (/cherch\w* (un |une )?(développeur|prestataire|agence)/i.test(text)) problems.push("ne jamais laisser entendre que l'entreprise cherche un prestataire");
  if (/ne souhaitez (pas être recontacté|plus de message)|sources publiques|proviennent de/i.test(core)) problems.push("la phrase d'opposition et la notice sont ajoutées par l'application : ne pas les écrire");
  return problems;
}

/** Ce que le CODE ajoute toujours autour du cœur du message (écrit par le modèle ou par /rediger) : salutation, signature, puis UNE ligne
 *  discrète (source, refus des messages suivants, lien vers la notice). Le nom du gérant et les coordonnées ne sortent donc jamais de l'application. */
export function wrapDraft(p: any, sender: Sender, core: string): string {
  const signature = [sender.name || MISSING_NAME, sender.company, sender.phone, sender.email, sender.website].filter(Boolean).join("\n");
  const hello = p.manager_name ? `Bonjour ${p.manager_name},` : "Bonjour,";
  return `${hello}\n\n${core.trim()}\n\n`
    + `Bien cordialement,\n${signature}\n\n--\n${informationNotice(p, sender)}`;
}

export function mailtoLink(to: string | null | undefined, subject: string, body: string): string {
  return `mailto:${encodeURIComponent(to ?? "")}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}
