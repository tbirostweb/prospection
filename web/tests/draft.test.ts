import { test } from "node:test";
import assert from "node:assert/strict";
import { MISSING_NAME, MISSING_NOTICE, buildDraft, draftIncomplete, informationNotice, mailtoLink } from "../lib/draft";

const PROSPECT = {
  company_name: "CAFE DE LA GARE", city: "TROYES", website_status: "UNREACHABLE", website_url: "https://exemple.test",
  issues: "[]", contact_source_url: "https://www.exemple.test/contact", activity_label: "Restauration",
};

test("brouillon : information art. 14 (source + lien de la notice) toujours présente", () => {
  const d = buildDraft(PROSPECT, { name: "Prénom Nom", privacy_url: "https://moi.test/confidentialite" })!;
  assert.ok(d);
  assert.match(d.body, /proviennent de sources publiques \(notamment exemple\.test\)/);
  assert.match(d.body, /https:\/\/moi\.test\/confidentialite/);
  assert.match(d.body, /ne souhaitez pas être recontacté/);
  assert.equal(draftIncomplete(d.body), false);
});

test("brouillon : notice ou identité manquante -> marqueur explicite et envoi bloqué", () => {
  const d = buildDraft(PROSPECT, {})!;
  assert.ok(d.body.includes(MISSING_NOTICE));
  assert.ok(d.body.includes(MISSING_NAME));
  assert.equal(draftIncomplete(d.body), true);
  const d2 = buildDraft(PROSPECT, { name: "Prénom Nom" })!;
  assert.equal(draftIncomplete(d2.body), true, "notice absente : toujours bloqué");
});

test("notice : source inconnue -> mention générique, jamais inventée", () => {
  assert.match(informationNotice({}, { privacy_url: "https://x.test/p" }), /sources publiques \(annuaires officiels/);
});

test("mailto : CR/LF et caractères spéciaux encodés (pas d'injection d'en-tête)", () => {
  const l = mailtoLink("a@b.test\r\nBcc: x@y.test", "Objet\r\nBcc: z@y.test", "corps&cc=evil@x.test");
  assert.doesNotMatch(l, /[\r\n]/);
  assert.ok(!l.includes("&cc="));
  assert.equal(l.split("?")[1].split("&").length, 2);
});
