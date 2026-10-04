// Brouillon d'e-mail PERSONNALISÉ, créé seulement quand tu cliques « Créer un brouillon » — jamais envoyé automatiquement.
// Construit UNIQUEMENT avec des faits réellement détectés sur l'entreprise (site non trouvé, problèmes objectifs du site, création récente…).
// Ton : court, courtois, sans fausse affirmation (« je peux me tromper »), toujours avec une phrase permettant de refuser tout nouveau contact.

import { siteSearched, verifiedSocials } from "./local";

export interface Sender { name?: string; company?: string; role?: string; phone?: string; email?: string; website?: string; pitch?: string; privacy_url?: string }

/** Marqueurs laissés dans un brouillon quand une information OBLIGATOIRE manque : tant qu'ils sont présents, l'envoi (mailto) est désactivé. */
export const MISSING_NOTICE = "[LIEN VERS VOTRE NOTICE D'INFORMATION À RENSEIGNER DANS RÉGLAGES]";
export const MISSING_NAME = "[Votre prénom et nom]";
export const draftIncomplete = (text: string | null | undefined) => !!text && (text.includes(MISSING_NOTICE) || text.includes(MISSING_NAME));

/** Information des personnes dont les coordonnées n'ont pas été collectées auprès d'elles (art. 14 RGPD) : source + lien vers la notice. */
export function informationNotice(p: any, sender: Sender = {}): string {
  let source = "";
  try { source = p.contact_source_url ? new URL(p.contact_source_url).hostname.replace(/^www\./, "") : ""; } catch { /* URL illisible */ }
  const origin = source ? `de sources publiques (notamment ${source})` : "de sources publiques (annuaires officiels des entreprises, site internet)";
  const url = sender.privacy_url?.trim() || MISSING_NOTICE;
  return `Vos coordonnées professionnelles proviennent ${origin}. Pour savoir comment elles sont utilisées et exercer vos droits (accès, rectification, effacement, opposition) : ${url}`;
}
export interface Draft { subject: string; body: string; facts: string[]; kind: string }

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
};
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

function monthsSince(d?: string | null): number | null {
  if (!d) return null;
  const x = new Date(d), n = new Date();
  return (n.getFullYear() - x.getFullYear()) * 12 + n.getMonth() - x.getMonth();
}

/** `p` : ligne `local_prospects`. Renvoie null quand AUCUN fait objectif ne justifie un message (on n'invente jamais une faiblesse). */
/** « de Le Petit Bistrot » → « du Petit Bistrot », « de Les Délices » → « des Délices », « de L'Atelier » → « de l'Atelier ». */
export const deName = (name: string) => /^le\s/i.test(name) ? `du ${name.slice(3)}` : /^les\s/i.test(name) ? `des ${name.slice(4)}` : /^la\s/i.test(name) ? `de la ${name.slice(3)}` : /^l['’]/i.test(name) ? `de l'${name.slice(2)}` : `de ${name}`;

export function buildDraft(p: any, sender: Sender = {}): Draft | null {
  const name = cap((p.trade_name || p.company_name || "").trim());
  const city = p.city ? cap(p.city) : null;
  const where = city ? ` à ${city}` : "";
  const issues: any[] = j(p.issues) ?? [];
  const ev = j(p.website_evidence) ?? {};
  const socials: string[] = verifiedSocials(p).map((x) => x.url);                // vérifiés seulement : jamais un profil voisin
  const status = p.website_status;
  const facts: string[] = [];
  let opening = "", kind = "";
  const created = monthsSince(p.company_created_at);
  const recent = created != null && created >= 0 && created <= 12;
  const sigs: any[] = j(p.buy_signals) ?? [];
  const event = sigs.find((x) => EVENT_OPENINGS[x.code]);
  const ref = recent || event ? "votre entreprise" : `${name}${where}`;          // on vient de nommer l'entreprise dans les félicitations : pas de répétition

  if (status === "NOT_FOUND" && !siteSearched(p)) {
    return null;                                                                   // recherche trop maigre : « pas de site » n'est pas encore un fait
  } else if (status === "NOT_FOUND") {
    kind = "sans_site";
    const onSocial = socials.length ? ` Je vois que vous êtes présent${socials.some((u) => /facebook/.test(u)) ? " sur Facebook" : " sur les réseaux sociaux"}, mais` : " En faisant quelques recherches,";
    opening = `${onSocial} je n'ai pas trouvé de site internet pour ${ref} (je peux me tromper : si vous en avez un, ne tenez pas compte de ce message).`;
    facts.push("aucun site internet officiel trouvé lors de mes recherches");
  } else if (status === "UNREACHABLE") {
    kind = "site_inaccessible";
    opening = ` En voulant consulter le site de ${name}, je n'ai pas pu y accéder lors de ma vérification${p.website_url ? ` (${p.website_url})` : ""}.`;
    facts.push("site inaccessible lors de ma vérification");
  } else if ((status === "CONFIRMED" || status === "PROBABLE") && issues.length) {
    const codes = new Set(issues.map((i) => i.code));
    const found = PRIORITY.filter((c) => codes.has(c)).map((c) => ISSUE_PHRASES[c]).slice(0, 3);
    if (!found.length) return null;
    kind = "site_ameliorable";
    facts.push(...found);
    opening = ` J'ai regardé rapidement le site de ${name}${p.website_url ? ` (${p.website_url.replace(/^https?:\/\//, "").replace(/\/$/, "")})` : ""} — uniquement ce qui est visible publiquement — et j'ai relevé quelques points qui pourraient vous faire perdre des clients :`;
  } else {
    return null;
  }

  const first = event ? `${EVENT_OPENINGS[event.code](name, where)}${opening}`
    : recent ? `Félicitations pour le lancement ${deName(name)}${where} !${opening}` : opening.trim();
  const who = [sender.name, sender.role ? sender.role : "développeur web", sender.company ? `(${sender.company})` : ""].filter(Boolean).join(", ").replace(", (", " (");
  const activity = p.activity_label ? p.activity_label.split(",")[0].toLowerCase() : null;
  const pitch = sender.pitch?.trim()
    || (kind === "sans_site"
      ? `Je crée des sites simples et efficaces pour les ${activity ?? "entreprises"} de la région : être trouvé sur Google, présenter vos services et être joignable facilement depuis un téléphone.`
      : kind === "site_inaccessible"
        ? "Je peux vous aider à le remettre en ligne rapidement, et si besoin à le moderniser pour qu'il soit trouvé et consultable sur téléphone."
        : "Je peux corriger ces points, ou vous proposer une version plus moderne de votre site, selon ce qui a du sens pour vous.");
  const list = kind === "site_ameliorable" ? "\n" + facts.map((f) => `- ${f}`).join("\n") : "";
  const signature = [sender.name || MISSING_NAME, sender.company, sender.phone, sender.email, sender.website].filter(Boolean).join("\n");
  const hello = p.manager_name ? `Bonjour ${p.manager_name},` : "Bonjour,";
  const body = `${hello}\n\n${first}${list}\n\nJe suis ${who || "développeur web indépendant"}. ${pitch}\n\n`
    + "Seriez-vous d'accord pour un échange de 10 minutes, sans engagement ? Je peux aussi vous envoyer quelques exemples de réalisations.\n\n"
    + "Si vous ne souhaitez pas être recontacté(e), dites-le-moi simplement et je ne vous écrirai plus.\n\n"
    + `Bien cordialement,\n${signature}\n\n--\n${informationNotice(p, sender)}`;
  const subject = kind === "sans_site" ? `${name} : votre présence en ligne${city ? ` à ${city}` : ""}`
    : kind === "site_inaccessible" ? `${name} : votre site semble inaccessible`
    : `${name} : quelques points à améliorer sur votre site`;
  return { subject, body, facts, kind: event ? `${event.code}_${kind}` : kind };       // type mesuré par l'apprentissage (quel message obtient des réponses)
}

export function mailtoLink(to: string | null | undefined, subject: string, body: string): string {
  return `mailto:${encodeURIComponent(to ?? "")}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}
