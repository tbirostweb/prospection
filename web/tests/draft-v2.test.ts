import { test } from "node:test";
import assert from "node:assert/strict";
import { buildDraft, checkWrittenCore, offerParagraphs, photosOffered, wrapDraft } from "../lib/draft";

const P = { company_name: "PLOMBERIE DURAND", city: "TROYES", website_status: "UNREACHABLE", website_url: "https://durand.test", issues: "[]",
  activity_label: "Plombiers", manager_name: "Paul Durand", contact_source_url: "https://durand.test/contact" };
const SENDER = { name: "Théo Birost", privacy_url: "https://moi.test/confidentialite", website: "https://birostweb.test/" };
const OFFER = { enabled: true, area: "basé près de Troyes", launch_price: 690, normal_price: 990, launch_slots_left: 3 };

test("offre v3 : ni paiement ni domaine au premier message, tarif de lancement sans aveu, photos seulement si demandées", () => {
  const launch = offerParagraphs(OFFER).join(" ");
  assert.doesNotMatch(launch, /acompte|70 %|abonnement|domaine|hébergement|rien (à payer )?avant/);
  assert.doesNotMatch(launch, /avis client/, "pas de « je n'ai pas encore d'avis client »");
  assert.match(launch, /offre de lancement : pour les 3 prochaines entreprises, le site est à 690 € au lieu de 990 €\./);
  assert.doesNotMatch(launch, /avis/, "jamais de réduction contre un avis (règles Google)");
  assert.doesNotMatch(launch, /photos/);
  assert.match(launch, /En option, je peux aussi créer ou compléter votre fiche Google\./, "fiche Google : une option, pas l'offre de base");
  assert.doesNotMatch(launch, /mettre à jour votre fiche Google/, "ne pas supposer qu'une fiche existe");
  assert.doesNotMatch(launch, /votre fiche Google est mise à jour/);
  assert.match(offerParagraphs(OFFER, { photos: true }).join(" "), /En option, je peux aussi créer ou compléter votre fiche Google et passer faire les photos\./);
  const after = offerParagraphs({ ...OFFER, launch_slots_left: 0 }).join(" ");
  assert.doesNotMatch(after, /690|lancement/);
  assert.match(after, /Le site est à 990 €\./);
  assert.doesNotMatch(offerParagraphs({ enabled: true }).join(" "), /€/, "sans prix réglé : aucune phrase de prix");
  assert.doesNotMatch(offerParagraphs(OFFER, { photos: true }).join(" "), /[—–]/, "pas de tiret long (règle humanizer)");
});

test("photos : métiers concernés et proches seulement, jamais les photographes", () => {
  assert.equal(photosOffered({ activity_key: "coiffure", distance_km: "4.0" }), true);
  assert.equal(photosOffered({ activity_key: "garages", distance_km: 25 }), false, "trop loin (20 km par défaut)");
  assert.equal(photosOffered({ activity_key: "garages", distance_km: 25 }, { photos_max_km: 30 }), true);
  assert.equal(photosOffered({ activity_key: "photographes", distance_km: 1 }), false);
  assert.equal(photosOffered({ activity_key: "coiffure", distance_km: null }), false, "distance inconnue : on ne propose pas");
});

test("brouillon v2 : offre présente si activée, ancien texte sinon ; cœur séparé de l'enveloppe", () => {
  const v2 = buildDraft(P, SENDER, OFFER)!;
  assert.match(v2.body, /Je suis Théo Birost, développeur web, basé près de Troyes\./);
  assert.match(v2.body, /mes réalisations et mes tarifs sur https:\/\/birostweb\.test, et y demander un devis ou réserver un appel ou une visio/);
  assert.ok(!v2.core.includes("Paul Durand") && !v2.core.includes("ne souhaitez"), "le cœur ne contient ni le gérant ni l'opposition");
  assert.ok(v2.body.startsWith("Bonjour Paul Durand,") && v2.body.includes("ne souhaitez plus de message de ma part"));
  const v1 = buildDraft(P, SENDER)!;
  assert.match(v1.body, /échange de 10 minutes/);
  assert.doesNotMatch(v1.body, /abonnement obligatoire/);
});

test("garde-fous /rediger : chiffres et liens limités à la référence, mentions interdites", () => {
  const ref = buildDraft(P, SENDER, OFFER)!;
  const ok = "En voulant consulter le site de Plomberie Durand, je n'ai pas pu y accéder. Je m'occupe de tout et j'écris les textes. "
    + "C'est mon offre de lancement : pour les 3 prochaines entreprises, le site est à 690 € au lieu de 990 €. "
    + "Mes tarifs sont sur https://birostweb.test, où vous pouvez réserver un appel.";
  assert.deepEqual(checkWrittenCore(ok, "Plomberie Durand : votre site", ref), []);
  const bad = checkWrittenCore(ok + " 87 % de vos clients vous cherchent sur https://stats.test, résultat garanti.", "Objet", ref);
  assert.ok(bad.some((x) => x.includes("87")), "chiffre inventé refusé");
  assert.ok(bad.some((x) => x.includes("stats.test")), "lien inventé refusé");
  assert.ok(bad.some((x) => x.includes("garantie")), "garantie refusée");
  assert.ok(checkWrittenCore("trop court", "x", ref).some((x) => x.includes("trop court")));
  assert.ok(checkWrittenCore(ok + " Si vous ne souhaitez pas être recontacté, dites-le.", "x", ref).some((x) => x.includes("ajoutées par l'application")));
});

test("élision devant une voyelle : « le lancement d'Elio », jamais « de Elio »", () => {
  const p = { company_name: "ELIO IMMOBILIER", city: "FONTAINE-LES-GRES", website_status: "NOT_FOUND", website_absence_confidence: 0.85,
    website_search_tried: 8, website_search_total: 10, company_created_at: new Date(Date.now() - 60 * 86400000).toISOString().slice(0, 10), issues: "[]" };
  const d = buildDraft(p, SENDER, OFFER)!;
  assert.match(d.body, /lancement d'Elio Immobilier/);
  assert.match(d.body, /sauf erreur de ma part\./);
  assert.doesNotMatch(d.body, /ignorez|ne tenez pas compte/);
});

test("enveloppe : salutation, opposition, signature et notice toujours ajoutées par le code", () => {
  const body = wrapDraft(P, SENDER, "Cœur du message.");
  assert.ok(body.startsWith("Bonjour Paul Durand,\n\nCœur du message."));
  assert.match(body, /ne souhaitez plus de message de ma part/);
  const tail = body.slice(body.indexOf("Théo Birost"));
  assert.match(tail, /\n\n--\nCoordonnées trouvées sur des sources publiques .*En savoir plus : https:\/\/moi\.test\/confidentialite$/, "une seule ligne, sous la signature");
  assert.doesNotMatch(body.slice(0, body.indexOf("Bien cordialement")), /sources publiques|ne souhaitez/, "rien dans le corps du message");
  assert.match(body, /Théo Birost/);
  assert.match(body, /https:\/\/moi\.test\/confidentialite/);
});
