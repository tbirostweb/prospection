// Fiche de VENTE d'un prospect : pourquoi lui, pourquoi maintenant, quel argument, quelle action, quel message.
// Construite UNIQUEMENT à partir des données déjà en base (score, signaux, audit, SIRENE, contacts) : aucune source externe, rien d'inventé.
// Une donnée absente n'est jamais comblée ; sans fait objectif, pas de message conseillé.
import { EVENT_OPENINGS, ISSUE_PHRASES, PRIORITY, Sender, cap, deName } from "./draft";
import { NETWORK_ICONS, bestTime, followupDue, monthsSince, siteSearched, verifiedSocials } from "./local";

export interface Brief {
  priority: string; summary: string; whyHim: string[]; whyNow: string | null; angle: string;
  action: { label: string; detail: string | null }; message: { subject: string; body: string } | null; messageNote: string;
  links: { icon: string; label: string; url: string; note: string }[]; pending: string | null;
}
const SITE_SIGNALS = new Set(["new_no_site", "site_down", "socials_only", "no_contact_way", "site_builder"]);      // déjà dits par l'état du site

const j = (v: any) => (typeof v === "string" ? (() => { try { return JSON.parse(v); } catch { return null; } })() : v);
const EMPLOYEES: Record<string, string> = { "01": "1 à 2 salariés", "02": "3 à 5 salariés", "03": "6 à 9 salariés", "11": "10 à 19 salariés", "12": "20 à 49 salariés" };
const STOREFRONT = new Set(["restaurants", "bars", "boulangeries", "coiffure", "beaute", "commerces", "boutiques", "fleuristes", "opticiens", "soins_personnels", "garages"]);
const ANGLES: Record<string, string> = {
  takeover: "nouveau propriétaire : un site à son image pour marquer la reprise et garder les clients de l'ancien",
  rename: "nouveau nom : que les clients le retrouvent sur Google sous ce nom",
  move: "nouvelle adresse : être trouvé au bon endroit (Google, plan d'accès, horaires)",
  new_establishment: "nouvel établissement : faire connaître cette adresse aux clients du coin",
  site_down: "son site ne répond plus : il perd des clients en ce moment, remise en ligne rapide",
  new_no_site: "démarrage : attirer ses premières demandes locales",
  site_diy: "passer d'un site gratuit ou d'annuaire à un vrai site à son nom",
  socials_only: "il a déjà des clients sur les réseaux : leur donner une vitrine trouvable sur Google",
  no_contact_way: "ses visiteurs ne peuvent pas lui écrire : ajouter un moyen simple de le contacter",
  site_builder: "passer d'un site « fait soi-même » à un site pro, pensé pour être trouvé et pour convertir",
};
const ISSUE_ANGLES: [string, string][] = [
  ["free_subdomain", "son site est sur une adresse gratuite : un vrai site à son nom, plus crédible et mieux trouvé sur Google"],
  ["directory_site", "son site est fourni par un annuaire : un site à lui, qui lui ressemble, souvent pour moins cher que l'abonnement"],
  ["no_viewport", "son site s'affiche mal sur téléphone, là où ses clients le cherchent"],
  ["no_https", "son site est affiché « non sécurisé » par les navigateurs"],
  ["slow_response", "son site est lent : les visiteurs partent avant de le voir"],
  ["noindex", "son site est invisible sur Google (non indexé)"],
  ["robots_blocks_all", "son site est invisible sur Google (bloqué aux moteurs)"],
  ["old_tech", "son site repose sur des technologies anciennes : le moderniser"],
  ["site_builder", "passer d'un site « fait soi-même » à un site pro, pensé pour être trouvé et pour convertir"],
];

function learningLine(sd: any): string | null {
  const l = (sd?.details ?? []).find((d: any) => d.key === "learning");
  if (!l || !(l.points > 0)) return null;
  const first = String(l.detail).split(" : ").slice(1).join(" : ").split(" · ")[0];
  return first ? `ce type de prospect te répond bien (${first})` : null;
}

/** `p` : ligne `local_prospects` complète. `sender` : réglage `local_sender` (signature du message conseillé). */
export function salesBrief(p: any, sender: Sender = {}): Brief {
  const sd = j(p.score_details) ?? {};
  const signals: any[] = j(p.buy_signals) ?? [];
  const issues: any[] = j(p.issues) ?? [];
  const name = cap((p.trade_name || p.company_name || "").trim());
  const city = p.city ? cap(p.city) : null;
  const created = monthsSince(p.company_created_at);
  const status = p.website_status;
  const siteOk = status === "CONFIRMED" || status === "PROBABLE";
  const codes = new Set(issues.map((i) => i.code));
  const serious = issues.filter((i) => i.severity === "high" || i.severity === "medium");
  const searched = siteSearched(p);
  const social = verifiedSocials(p);
  const blocked = p.do_not_contact || p.excluded_reason || p.is_chain || p.category === "IGNORER";

  // Résumé en une ligne : 3 faits au plus
  const facts: string[] = [];
  if (created != null && created >= 0 && created <= 24) facts.push(created === 0 ? "créé ce mois-ci" : `créé il y a ${created} mois`);
  if (status === "NOT_FOUND") facts.push(searched ? "aucun site trouvé" : "site pas encore vérifié");
  else if (status === "UNREACHABLE") facts.push("site en panne");
  else if (siteOk && (p.modernization_opportunity === "HIGH" || p.modernization_opportunity === "MEDIUM")) facts.push("site à moderniser");
  else if (siteOk && serious.length) facts.push(`${serious.length} problème(s) sur son site`);
  else if (siteOk) facts.push("site correct");
  if (EMPLOYEES[p.employee_range]) facts.push(EMPLOYEES[p.employee_range]);
  const event = signals.find((x) => !SITE_SIGNALS.has(x.code));
  if (event && facts.length < 3) facts.push(event.label.replace(/ \(BODACC\)$/, "").toLowerCase());

  const priority = p.do_not_contact ? "ne pas contacter" : blocked ? "écarté" : ({ TRES_BON: "très élevée", A_CONTACTER: "élevée", A_EXAMINER: "moyenne" } as Record<string, string>)[p.category] ?? "faible";

  // Pourquoi lui (1-2 raisons)
  const whyHim: string[] = [];
  if (status === "NOT_FOUND" && searched) whyHim.push(`aucun site trouvé${p.website_search_total ? ` (${p.website_search_tried}/${p.website_search_total} recherches)` : ""}${social.length ? `, mais actif sur ${social.map((x) => x.network).join(" et ")}` : ""}`);
  else if (status === "NOT_FOUND") whyHim.push("site pas encore trouvé, mais la recherche est incomplète : à vérifier avant de l'affirmer");
  else if (status === "UNREACHABLE") whyHim.push("son site ne répond plus");
  else if (siteOk && serious.length) whyHim.push(`son site a des défauts concrets : ${serious.slice(0, 2).map((i) => i.label.toLowerCase()).join(", ")}`);
  if (p.budget_level === "élevé") whyHim.push("budget probable élevé (" + (String((sd.details ?? []).find((d: any) => d.key === "budget")?.detail ?? "").split(" — ")[1]?.split(" · ")[0] ?? "métier à forte valeur") + ")");
  const learned = learningLine(sd);
  if (learned) whyHim.push(learned);
  if (p.distance_km != null && Number(p.distance_km) <= 5) whyHim.push(`tout près : ${Number(p.distance_km).toFixed(1).replace(".", ",")} km`);

  // Pourquoi maintenant
  const top = signals[0];
  // Le même défaut sert à « pourquoi maintenant », à l'argument et au message : le plus parlant pour un commerçant d'abord.
  const issueCodes = [...ISSUE_ANGLES.map(([c]) => c), ...PRIORITY].filter((c, i, a) => codes.has(c) && a.indexOf(c) === i && ISSUE_PHRASES[c]);
  const issueNow = issueCodes[0];
  const whyNow = top ? `${top.label} — ${top.detail}` : issueNow && siteOk ? `problème détecté : ${ISSUE_PHRASES[issueNow]}` : null;

  // Argument
  const issueAngle = siteOk ? ISSUE_ANGLES.find(([c]) => codes.has(c))?.[1] : undefined;
  const activity = p.activity_label ? String(p.activity_label).split(",")[0].toLowerCase() : null;
  let angle = (top && ANGLES[top.code]) || issueAngle
    || (status === "NOT_FOUND" && !searched ? "vérifier d'abord s'il a un site (recherche incomplète), puis adapter l'approche" : "")
    || (status === "NOT_FOUND" ? `être trouvé sur Google par les clients qui cherchent ${activity ? `des ${activity}` : "ses services"}${city ? ` à ${city}` : ""}` : "")
    || (siteOk && (p.seo_score ?? 100) < 75 ? "mieux ressortir sur Google dans sa zone" : "")
    || "pas d'angle évident : site déjà correct, prospect secondaire";
  if (p.budget_level === "élevé" && !angle.startsWith("pas d'angle")) angle += " — un seul client gagné peut rembourser le site";

  // Prochaine action
  const due = followupDue({ status: p.status, contacted_at: p.contacted_at ? String(p.contacted_at) : null, followups: p.followups });
  const when = bestTime(p.activity_key);
  const phone = !!p.phone, email = !!p.email;
  let action: Brief["action"];
  if (blocked) action = { label: "Aucune", detail: p.do_not_contact ? "a demandé à ne plus être contacté" : "écarté du ciblage (voir « Pourquoi ce score »)" };
  else if (p.status === "WON") action = { label: "Client obtenu", detail: null };
  else if (p.status === "LOST") action = { label: "Aucune", detail: "marqué perdu" };
  else if (p.status === "REPLIED" || p.status === "INTERESTED") action = { label: "Rappeler pour fixer un rendez-vous", detail: phone ? `appeler ${when}` : "répondre à son message" };
  else if (p.status === "CONTACTED") action = due
    ? { label: due.kind === "followup" ? due.label : "Clore : marquer « sans réponse »", detail: due.kind === "followup" ? (phone ? `par téléphone, ${when}` : "par e-mail, en répondant au premier message") : null }
    : { label: "Attendre la réponse", detail: "relance prévue à J+3 puis J+10" };
  else if (phone && email) action = { label: "Appel + e-mail", detail: `appeler ${when}, puis envoyer le message pour garder une trace` };
  else if (phone) action = { label: "Appeler", detail: when };
  else if (STOREFRONT.has(p.activity_key) && p.distance_km != null && Number(p.distance_km) <= 15) action = { label: email ? "Passer sur place (ou e-mail)" : "Passer sur place", detail: `${p.address ?? city ?? ""}${p.address || city ? " · " : ""}${when}` };
  else if (email) action = { label: "Envoyer un e-mail", detail: null };
  else if (p.contact_form) action = { label: "Formulaire de contact de son site", detail: null };
  else if (p.address) action = { label: "Passer sur place", detail: p.address };
  else action = { label: "Trouver un contact", detail: "aucune coordonnée connue" };

  // Message conseillé : court, seulement des faits connus. Aucun fait = aucun message.
  let opening: string | null = null;
  const where = city ? ` à ${city}` : "";
  if (top && EVENT_OPENINGS[top.code]) opening = EVENT_OPENINGS[top.code](name, where);
  else if (top?.code === "new_no_site" || (status === "NOT_FOUND" && created != null && created >= 0 && created <= 12)) opening = `Félicitations pour le lancement ${deName(name)}${where} !`;
  let fact: string | null = null;
  if (status === "NOT_FOUND" && searched) fact = `Je n'ai pas trouvé de site internet pour ${opening ? "votre entreprise" : `${name}${where}`} (je peux me tromper).`;
  else if (status === "UNREACHABLE") fact = `En voulant consulter votre site, je n'ai pas pu y accéder.`;
  else if (siteOk && issueNow) fact = `En regardant votre site, j'ai remarqué que ${issueCodes.slice(0, 2).map((c) => ISSUE_PHRASES[c]).join(", et que ")}.`;
  let message: Brief["message"] = null;
  let messageNote = "aucun fait vérifié ne justifie un message (on n'invente jamais une faiblesse)";
  const cold = ["DISCOVERED", "ENRICHED", "AUDITED", "QUALIFIED", "TO_CONTACT"].includes(p.status);
  const hello = p.manager_name ? `Bonjour ${p.manager_name},` : "Bonjour,";
  const signature = [sender.name || "[Votre prénom et nom]", ...(sender.phone ? [sender.phone] : [])];
  if (blocked) messageNote = p.do_not_contact ? "aucun message : a demandé à ne plus être contacté" : "aucun message : prospect écarté";
  else if (p.status === "CONTACTED" && fact) {
    if (due?.kind === "followup") {
      const first = p.contacted_at ? new Date(p.contacted_at).toLocaleDateString("fr-FR", { day: "numeric", month: "long" }) : null;
      message = { subject: `Re : ${name}`, body: [hello, "", `Je me permets de revenir vers vous au sujet de mon message${first ? ` du ${first}` : ""}. ${fact}`,
        "Seriez-vous disponible 10 minutes cette semaine pour en parler ?", "", "Si ce n'est pas le moment, dites-le-moi simplement et je ne vous relancerai plus.", "", ...signature].join("\n") };
    } else messageNote = "déjà contacté : attendre la prochaine relance";
  } else if (!cold) messageNote = "échange en cours : pas de message type, réponds-lui personnellement";
  if (cold && !blocked && fact) {
    const offer = status === "NOT_FOUND" ? `Je crée des sites simples pour ${activity ? `les ${activity}` : "les entreprises"} de la région, pour être trouvé sur Google et joignable depuis un téléphone.`
      : status === "UNREACHABLE" ? "Je peux vous aider à le remettre en ligne rapidement."
      : `Je peux corriger ${issueCodes.length > 1 ? "ces points" : "ce point"} ou moderniser votre site, selon ce qui a du sens pour vous.`;
    const body = [hello, "", [opening, fact].filter(Boolean).join(" "), offer,
      "Seriez-vous d'accord pour en parler 10 minutes, sans engagement ?", "", "Si vous ne souhaitez pas être recontacté(e), dites-le-moi simplement.",
      "", ...signature].join("\n");
    message = { subject: status === "NOT_FOUND" ? `${name} : votre présence en ligne` : status === "UNREACHABLE" ? `${name} : votre site semble inaccessible` : `${name} : un point sur votre site`, body };
  }

  // Liens en 1 clic : le site retenu et chaque profil VÉRIFIÉ
  const links: Brief["links"] = [];
  if (p.website_url && ["CONFIRMED", "PROBABLE", "UNREACHABLE"].includes(status))
    links.push({ icon: "🌐", label: status === "UNREACHABLE" ? "Site (en panne)" : "Site", url: p.website_url, note: status === "PROBABLE" ? "site probable, à confirmer" : "site officiel" });
  for (const x of social) links.push({ icon: NETWORK_ICONS[x.network] ?? "🔗", label: x.network, url: x.url, note: x.via === "site" ? "lié depuis son site officiel" : "trouvé par recherche (nom + ville vérifiés)" });
  const qualified = ["TRES_BON", "A_CONTACTER", "A_EXAMINER"].includes(p.category);
  const pending = !blocked && qualified && !p.deep_enriched_at ? "enrichissement approfondi en attente (réseaux, e-mail, dirigeant) : la fiche sera complétée au prochain passage" : null;

  const cap1 = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
  const summary = [facts.length ? cap1(facts.join(", ")) + "." : null, `Priorité ${priority}.`, `Angle : ${angle.split(" — ")[0]}.`, `Action : ${action.label.charAt(0).toLowerCase() + action.label.slice(1)}.`].filter(Boolean).join(" ");
  return { priority, summary, whyHim: whyHim.slice(0, 2), whyNow, angle, action, message, messageNote, links, pending };
}
