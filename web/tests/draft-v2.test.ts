import { test } from "node:test";
import assert from "node:assert/strict";
import { buildDraft, checkWrittenCore, offerParagraphs, wrapDraft } from "../lib/draft";

const P = { company_name: "PLOMBERIE DURAND", city: "TROYES", website_status: "UNREACHABLE", website_url: "https://durand.test", issues: "[]",
  activity_label: "Plombiers", manager_name: "Paul Durand", contact_source_url: "https://durand.test/contact" };
const SENDER = { name: "Théo Birost", privacy_url: "https://moi.test/confidentialite" };
const OFFER = { enabled: true, area: "basé près de Troyes", launch_price: 690, normal_price: 990, launch_slots_left: 3 };

test("offre v2 : prix de lancement et aveu honnête seulement s'il reste des places", () => {
  const launch = offerParagraphs(OFFER).join(" ");
  assert.match(launch, /rien avant la mise en ligne/);
  assert.match(launch, /aucun abonnement obligatoire/);
  assert.match(launch, /pas encore d'avis client/);
  assert.match(launch, /690 € au lieu de 990 €/);
  const after = offerParagraphs({ ...OFFER, launch_slots_left: 0 }).join(" ");
  assert.doesNotMatch(after, /avis client|690/);
  assert.match(after, /Le site est à 990 €\./);
  assert.doesNotMatch(offerParagraphs({ enabled: true }).join(" "), /€/, "sans prix réglé : aucune phrase de prix");
  assert.doesNotMatch(offerParagraphs(OFFER).join(" "), /[—–]/, "pas de tiret long (règle humanizer)");
});

test("brouillon v2 : offre présente si activée, ancien texte sinon ; cœur séparé de l'enveloppe", () => {
  const v2 = buildDraft(P, SENDER, OFFER)!;
  assert.match(v2.body, /Je suis Théo Birost, développeur web, basé près de Troyes\./);
  assert.match(v2.body, /appeler 15 minutes ou passer vous voir/);
  assert.ok(!v2.core.includes("Paul Durand") && !v2.core.includes("ne souhaitez pas"), "le cœur ne contient ni le gérant ni l'opposition");
  assert.ok(v2.body.startsWith("Bonjour Paul Durand,") && v2.body.includes("ne souhaitez pas être recontacté"));
  const v1 = buildDraft(P, SENDER)!;
  assert.match(v1.body, /échange de 10 minutes/);
  assert.doesNotMatch(v1.body, /abonnement obligatoire/);
});

test("garde-fous /rediger : chiffres et liens limités à la référence, mentions interdites", () => {
  const ref = buildDraft(P, SENDER, OFFER)!;
  const ok = "En voulant consulter le site de Plomberie Durand, je n'ai pas pu y accéder. Je m'occupe de tout : une heure sur place pour les photos, "
    + "j'écris les textes. Vous ne payez rien avant la mise en ligne. Pour les 3 prochaines entreprises, le site est à 690 € au lieu de 990 €. "
    + "Je peux vous appeler 15 minutes ou passer vous voir.";
  assert.deepEqual(checkWrittenCore(ok, "Plomberie Durand : votre site", ref), []);
  const bad = checkWrittenCore(ok + " 87 % de vos clients vous cherchent sur https://stats.test, résultat garanti.", "Objet", ref);
  assert.ok(bad.some((x) => x.includes("87")), "chiffre inventé refusé");
  assert.ok(bad.some((x) => x.includes("stats.test")), "lien inventé refusé");
  assert.ok(bad.some((x) => x.includes("garantie")), "garantie refusée");
  assert.ok(checkWrittenCore("trop court", "x", ref).some((x) => x.includes("trop court")));
  assert.ok(checkWrittenCore(ok + " Si vous ne souhaitez pas être recontacté, dites-le.", "x", ref).some((x) => x.includes("ajoutées par l'application")));
});

test("enveloppe : salutation, opposition, signature et notice toujours ajoutées par le code", () => {
  const body = wrapDraft(P, SENDER, "Cœur du message.");
  assert.ok(body.startsWith("Bonjour Paul Durand,\n\nCœur du message."));
  assert.match(body, /ne souhaitez pas être recontacté/);
  assert.match(body, /Théo Birost/);
  assert.match(body, /https:\/\/moi\.test\/confidentialite/);
});
