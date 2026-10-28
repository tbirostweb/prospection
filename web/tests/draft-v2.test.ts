import { test } from "node:test";
import assert from "node:assert/strict";
import { buildDraft, sanitizeOffer, checkWrittenCore, deName, offerParagraphs, photosOffered, salutation, wrapDraft } from "../lib/draft";
import { readFileSync } from "node:fs";
import path from "node:path";

const P = { company_name: "PLOMBERIE DURAND", city: "TROYES", website_status: "UNREACHABLE", website_url: "https://durand.test", issues: "[]",
  activity_label: "Plombiers", manager_name: "Paul Durand", contact_source_url: "https://durand.test/contact" };
const SENDER = { name: "Théo Birost", privacy_url: "https://moi.test/confidentialite", website: "https://birostweb.test/" };
const OFFER = { enabled: true, area: "basé près de Troyes", launch_price: 690, normal_price: 990, launch_slots_left: 3 };

test("offre v4 : jamais de prix (création comme refonte), renvoi vers la page des offres, photos seulement si demandées", () => {
  const launch = offerParagraphs(OFFER, { link: "https://birostweb.test" }).join(" ");
  assert.doesNotMatch(launch, /acompte|70 %|abonnement|domaine|hébergement|rien (à payer )?avant/);
  assert.doesNotMatch(launch, /€|690|990|lancement|au lieu de/, "aucun prix ni réduction, même avec un prix de lancement réglé");
  assert.doesNotMatch(launch, /https?:|tarifs sont détaillés/, "plus de lien vers les offres dans le corps (le site est dans la signature)");
  assert.doesNotMatch(launch, /avis/, "jamais de réduction contre un avis (règles Google)");
  assert.doesNotMatch(launch, /photos/);
  assert.match(launch, /En option, je peux aussi créer ou compléter votre fiche Google\./, "fiche Google : une option, pas l'offre de base");
  assert.doesNotMatch(launch, /mettre à jour votre fiche Google/, "ne pas supposer qu'une fiche existe");
  assert.doesNotMatch(launch, /votre fiche Google est mise à jour/);
  assert.match(offerParagraphs(OFFER, { photos: true }).join(" "), /En option, je peux aussi créer ou compléter votre fiche Google et passer faire les photos\./);
  assert.doesNotMatch(offerParagraphs({ ...OFFER, launch_slots_left: 0 }).join(" "), /€|990/);
  assert.doesNotMatch(offerParagraphs({ enabled: true }).join(" "), /€|tarifs sont détaillés/, "sans lien : ni prix ni renvoi");
  assert.doesNotMatch(offerParagraphs(OFFER, { photos: true }).join(" "), /[—–]/, "pas de tiret long (règle humanizer)");
});

test("formule d'appel : jamais de nom ni de parenthèses, Monsieur/Madame seulement si la civilité est connue", () => {
  const ref: any = { ...buildDraft(P, SENDER, OFFER)!, kind: "sans_site" };
  assert.equal(salutation({ manager_name: "Nicolas George (George)" }), "Bonjour,");
  assert.equal(salutation({ manager_name: "Nicolas George", manager_civility: "M" }), "Bonjour Monsieur,");
  assert.equal(salutation({ manager_civility: "Mme" }), "Bonjour Madame,");
  assert.ok(wrapDraft({ ...P, manager_name: "Nicolas George (George)" }, SENDER, "Cœur.").startsWith("Bonjour,\n\n"));
  const rest = " Je m'appelle Théo, développeur web près de Troyes, et je n'ai pas trouvé de site pour votre entreprise. Un site pourrait présenter vos prestations. Je m'occupe de tout, du design aux textes. Est-ce que ce serait un projet qui vous intéresserait ? Je vous partage mon site pour plus d'informations.";
  for (const bad of ["Bonjour Nicolas George (George),", "Bonjour,", "Bonjour Monsieur,", "Nicolas George,", "Je suis Théo (Birostweb)."])
    assert.ok(checkWrittenCore(bad + "\n\n" + rest, "Objet", ref).length > 0, bad);
  assert.deepEqual(checkWrittenCore(rest.trim(), "Objet", ref).filter((x) => /première ligne|salutation/.test(x)), []);
  const route = readFileSync(path.resolve(__dirname, "..", "..", "app/api/local/agent/queue/route.ts"), "utf-8");
  assert.match(route, /salutation: salutation\(p\)/);
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
  assert.doesNotMatch(v2.core, /Mes offres|€/, "aucun prix, aucun renvoi vers les offres dans le corps");
  const creation = buildDraft({ ...P, website_status: "NOT_FOUND", website_url: null, website_search: "x" }, SENDER, OFFER);
  if (creation) {
    assert.doesNotMatch(creation.core, /https?:|Mes offres/, "création : aucun lien dans le corps");
    assert.doesNotMatch(creation.body, /€|au lieu de|lancement/, "création : aucun prix");
  }
  assert.ok(!v2.core.includes("Paul Durand") && !v2.core.includes("ne souhaitez"), "le cœur ne contient ni le gérant ni l'opposition");
  assert.ok(v2.body.startsWith("Bonjour,\n") && v2.body.includes("ne souhaitez plus de message de ma part"));
  const v1 = buildDraft(P, SENDER)!;
  assert.match(v1.body, /échange de 10 minutes/);
  assert.doesNotMatch(v1.body, /abonnement obligatoire/);
});

test("garde-fous /rediger : chiffres et liens limités à la référence, mentions interdites", () => {
  const ref: any = { ...buildDraft(P, SENDER, OFFER)!, kind: "sans_site" };
  const ok = "En voulant consulter le site de Plomberie Durand, je n'ai pas pu y accéder. Je m'occupe de tout et j'écris les textes. "
    + "Le site est en ligne en deux à quatre semaines, et vous n'avez qu'à relire. "
    + "Est-ce que ce serait un projet qui vous intéresserait ? Je vous partage mon site pour plus d'informations.";
  assert.deepEqual(checkWrittenCore(ok, "Plomberie Durand : votre site", ref), []);
  assert.ok(checkWrittenCore(ok + " Le site est à 690 € au lieu de 990 €.", "x", ref).some((x) => x.includes("aucun prix")), "prix refusé, même en création");
  const bad = checkWrittenCore(ok + " 87 % de vos clients vous cherchent sur https://stats.test, résultat garanti.", "Objet", ref);
  assert.ok(bad.some((x) => x.includes("87")), "chiffre inventé refusé");
  assert.ok(bad.some((x) => x.includes("stats.test")), "lien refusé");
  assert.ok(checkWrittenCore(ok + " Mes offres sont sur https://birostweb.test.", "x", ref).some((x) => x.includes("aucun lien")), "aucun lien, même celui du site");
  assert.ok(bad.some((x) => x.includes("garantie")), "garantie refusée");
  assert.ok(checkWrittenCore("trop court", "x", ref).some((x) => x.includes("trop court")));
  assert.ok(checkWrittenCore(ok + " Si vous ne souhaitez pas être recontacté, dites-le.", "x", ref).some((x) => x.includes("ajoutées par l'application")));
});

test("élision devant une voyelle : « d'Atelier Exemple », jamais « de Atelier »", () => {
  assert.equal(deName("Atelier Exemple"), "d'Atelier Exemple");
  assert.equal(deName("Le Petit Bistrot"), "du Petit Bistrot");
  assert.equal(deName("Garage Martin"), "de Garage Martin");
});

test("jamais de « félicitations pour le lancement » d'après la seule date d'immatriculation", () => {
  const p = { company_name: "ATELIER EXEMPLE", city: "VILLE-TEST", website_status: "NOT_FOUND", website_absence_confidence: 0.85,
    website_search_tried: 8, website_search_total: 10, company_created_at: new Date(Date.now() - 60 * 86400000).toISOString().slice(0, 10), issues: "[]",
    provenance: { site_recheck: { status: "NOT_FOUND" } } };   // absence tranchée (recherche + revérification) : message de création
  const d = buildDraft(p, SENDER, OFFER)!;
  assert.doesNotMatch(d.body, /Félicitations|lancement d'Atelier/);
  assert.match(d.body, /sauf erreur de ma part\./);
  assert.doesNotMatch(d.body, /ignorez|ne tenez pas compte/);
});

test("offre du code : pas de bilan à trois mois tant qu'il n'est pas confirmé", () => {
  const text = offerParagraphs(OFFER, { photos: true }).join(" ");
  assert.doesNotMatch(text, /bilan|trois mois/);
  assert.match(text, /en ligne en deux à quatre semaines/);
});

test("file /rediger : jamais la raison sociale (elle contient le nom d'un entrepreneur individuel)", () => {
  const src = readFileSync(path.resolve(__dirname, "..", "..", "app/api/local/agent/queue/route.ts"), "utf-8")   // .test-dist/tests → web;
  assert.doesNotMatch(src, /raison_sociale/);
  assert.match(src, /nom_commercial/);
});

test("enveloppe : salutation, opposition, signature et notice toujours ajoutées par le code", () => {
  const body = wrapDraft(P, SENDER, "Cœur du message.");
  assert.ok(body.startsWith("Bonjour,\n\nCœur du message.") && !body.includes("Paul Durand"));
  assert.match(body, /ne souhaitez plus de message de ma part/);
  const tail = body.slice(body.indexOf("Théo Birost"));
  assert.match(tail, /\n\n--\nCoordonnées trouvées sur des sources publiques .*En savoir plus : https:\/\/moi\.test\/confidentialite$/, "une seule ligne, sous la signature");
  assert.doesNotMatch(body.slice(0, body.indexOf("Bien cordialement")), /sources publiques|ne souhaitez/, "rien dans le corps du message");
  assert.match(body, /Théo Birost/);
  assert.match(body, /https:\/\/moi\.test\/confidentialite/);
});

test("site existant avec défauts vérifiés : refonte proposée, jamais « corriger », année du © citée telle quelle", () => {
  const site = { ...P, website_status: "CONFIRMED", website_url: "https://www.garage-exemple.test/", site_year: 2015,
    issues: JSON.stringify([{ code: "no_viewport" }, { code: "meta_description_missing" }, { code: "canonical_missing" }]) };
  const d = buildDraft(site, SENDER, OFFER)!;
  assert.equal(d.kind, "site_ameliorable");
  assert.match(d.facts[0], /date de 2015/);
  assert.ok(d.facts.includes("l'affichage sur téléphone ne semble pas adapté") && d.facts.length === 3);
  assert.match(d.core, /refonte/);
  assert.doesNotMatch(d.core, /corriger|—|perdre des clients/);
  assert.deepEqual(checkWrittenCore("J'ai regardé votre site : la mention de copyright date de 2015 et l'affichage sur téléphone ne semble pas adapté. Je m'appelle Théo, je suis développeur web près de Troyes. Une refonte vous intéresserait ?", "Votre site", d), []);
  assert.equal(buildDraft({ ...site, issues: "[]", site_year: new Date().getFullYear() - 1 }, SENDER, OFFER), null);   // site récent : pas de refonte à proposer
});

test("site sans défaut relevé : message prudent « site_refonte », aucune faiblesse inventée, adresse du site citée", () => {
  const site = { ...P, website_status: "CONFIRMED", website_url: "https://www.garage-exemple.test/", site_year: null, issues: "[]" };
  const d = buildDraft(site, SENDER, OFFER)!;
  assert.equal(d.kind, "site_refonte");
  assert.match(d.core, /garage-exemple\.test/);
  assert.doesNotMatch(d.core, /je n'ai pas trouvé de site|pas de site|lent|ancien|dépassé|obsolète|mauvais|problème|défaut relevé|—/i);
  assert.match(d.core, /regard neuf/);
  assert.match(d.core, /\?/);
  assert.ok(!d.kind.includes("sans_site"));
  assert.equal(buildDraft({ ...site, website_url: null }, SENDER, OFFER)!.kind, "neutre");   // site sans adresse : pas vérifié, message neutre
  assert.equal(buildDraft({ ...site, site_year: new Date().getFullYear() }, SENDER, OFFER), null);   // site récent sans défaut : rien à proposer
  const old = buildDraft({ ...site, site_year: 2015, issues: null }, SENDER, OFFER)!;
  assert.equal(old.kind, "site_ameliorable");   // un fait vérifié (© 2015) reste cité comme fait
});

test("file de rédaction : les fiches avec site (CONFIRMED/PROBABLE/UNREACHABLE) entrent même en « À examiner », exclusions gardées", async () => {
  const src = readFileSync(path.resolve(__dirname, "..", "..", "lib/localdb.ts"), "utf-8");
  const CONTACT_QUEUE_SQL = src.slice(src.indexOf("export const CONTACT_QUEUE_SQL"), src.indexOf("/** Page « Entreprises »"));
  assert.match(CONTACT_QUEUE_SQL, /website_status IN \('CONFIRMED','PROBABLE','UNREACHABLE'\)/);
  for (const guard of ["p.do_not_contact = 0", "p.excluded_reason IS NULL", "p.status <> 'DO_NOT_CONTACT'", "p.is_chain = 0", "p.contacted_at IS NULL", "<> 'IGNORER'"])
    assert.ok(CONTACT_QUEUE_SQL.includes(guard), guard);
  assert.doesNotMatch(CONTACT_QUEUE_SQL, /'(LOST|WON|CONTACTED)'/);   // statuts fermés absents de la liste des statuts admis
  const route = readFileSync(path.resolve(__dirname, "..", "..", "app/api/local/agent/queue/route.ts"), "utf-8");
  assert.match(route, /isRedesignKind/);
});

test("refonte : aucun prix, renvoi vers le lien des offres ; création inchangée", () => {
  const O = { ...OFFER, offers_url: "https://offres.test/tarifs" };
  const refonte = buildDraft({ ...P, site_review: "CONFIRMED" }, SENDER, O)!;   // site confirmé par toi mais inaccessible = refonte
  assert.equal(refonte.kind, "site_inaccessible");
  assert.doesNotMatch(refonte.core, /€|au lieu de|lancement/);
  assert.doesNotMatch(refonte.core, /birostweb|offres\.test|Mes offres/, "aucun lien vers les offres dans le corps");
  assert.doesNotMatch(buildDraft(P, SENDER, { ...OFFER })!.core, /birostweb|Mes offres|€/);
  for (const p of [{ ...P, website_status: "NOT_FOUND", website_url: null, website_absence_confidence: 0.9, provenance: { site_recheck: { status: "NOT_FOUND" } } },
    { ...P, website_status: "NOT_FOUND", website_url: null }, P]) {   // création, puis neutre (recherche pas faite ; site injoignable)
    const d = buildDraft(p, SENDER, O)!;
    assert.doesNotMatch(d.core, /€|au lieu de|lancement/);
    assert.doesNotMatch(d.core, /offres\.test|birostweb|Mes offres/);
  }
  const ok = refonte.core;
  assert.deepEqual(checkWrittenCore(ok, "Objet", refonte).filter((x) => /chiffres|aucun prix/.test(x)), []);
  assert.ok(checkWrittenCore(ok + " Le site est à 690 €.", "Objet", refonte).some((x) => /aucun prix/.test(x)));
  assert.ok(checkWrittenCore(ok + " Une refonte démarre à 800 euros.", "Objet", refonte).some((x) => /aucun prix/.test(x)));
});

test("sanitizeOffer : lien http(s) seulement, prix bornés", () => {
  assert.equal(sanitizeOffer({ enabled: true, offers_url: "javascript:alert(1)" }), null);
  assert.equal(sanitizeOffer({ offers_url: "https://offres.test/x" })!.offers_url, "https://offres.test/x");
  assert.equal(sanitizeOffer({ offers_url: "" })!.offers_url, null);
  assert.equal(sanitizeOffer({ normal_price: 999999 })!.normal_price, 100000);
  assert.equal(sanitizeOffer(null), null);
});

test("e-mail d'abord : site incertain → message neutre, qui ne parle ni d'un site existant ni d'une absence de site, sans prix", () => {
  const O = { ...OFFER, offers_url: "https://offres.test/tarifs" };
  for (const p of [{ ...P, website_status: "UNCERTAIN" }, P, { ...P, website_status: "PROBABLE", site_review: "PENDING" }, { ...P, website_status: null, website_url: null }]) {
    const d = buildDraft(p, SENDER, O)!;
    assert.equal(d.kind, "neutre", String(p.website_status));
    assert.match(d.core, /présence sur internet de la Plomberie Durand|présence sur internet de Plomberie Durand/);
    assert.doesNotMatch(d.core, /votre site|site actuel|pas trouvé de site|pas de site|inaccessible|durand\.test|€/i);
    assert.doesNotMatch(d.core, /offres\.test|birostweb|Mes offres/);
    assert.deepEqual(checkWrittenCore(d.core, d.subject, d), [], "la référence passe ses propres garde-fous");
    for (const bad of [" J'ai regardé votre site.", " Je n'ai pas trouvé de site pour vous.", " Votre site actuel a vieilli.", " Votre site semble inaccessible."])
      assert.ok(checkWrittenCore(d.core + bad, d.subject, d).some((x) => /site incertain/.test(x)), bad);
  }
  const ev = buildDraft({ ...P, website_status: "UNCERTAIN", buy_signals: [{ code: "takeover" }] }, SENDER, O)!;
  assert.equal(ev.kind, "takeover_neutre");
  assert.match(ev.core, /^J'ai vu que vous aviez repris .* présence sur internet de votre entreprise\./s);
});

test("message de création : jamais d'allusion à un site existant", () => {
  const p = { ...P, website_status: "NOT_FOUND", website_url: null, website_absence_confidence: 0.9, provenance: { site_recheck: { status: "NOT_FOUND" } } };
  const d = buildDraft(p, SENDER, OFFER)!;
  assert.equal(d.kind, "sans_site");
  assert.deepEqual(checkWrittenCore(d.core, d.subject, d).filter((x) => /site/.test(x)), []);
  assert.ok(checkWrittenCore(d.core + " Votre site actuel a vieilli.", d.subject, d).some((x) => /site existant/.test(x)));
});

test("file e-mail d'abord : toute fiche avec e-mail jamais contactée, quel que soit le site, exclusions gardées", () => {
  const src = readFileSync(path.resolve(__dirname, "..", "..", "lib/localdb.ts"), "utf-8");
  const sql = src.slice(src.indexOf("export const EMAIL_QUEUE_SQL"));
  assert.doesNotMatch(sql.slice(0, sql.indexOf("ORDER BY")), /website_status/);
  for (const guard of ["p.do_not_contact = 0", "p.excluded_reason IS NULL", "p.is_chain = 0", "p.contacted_at IS NULL", "<> 'IGNORER'", "<> 'bounced'"])
    assert.ok(sql.includes(guard), guard);
  assert.doesNotMatch(sql.slice(0, sql.indexOf("ORDER BY")), /'(LOST|WON|CONTACTED|DO_NOT_CONTACT)'/);
});
