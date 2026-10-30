import test from "node:test";
import assert from "node:assert/strict";
import { checkSubject } from "../lib/draft";

test("objet accepté : court, naturel", () => {
  for (const s of ["Feinste à Fontvannes", "Une question pour Feinste", "Votre menuiserie et internet", "Le site de L'Essence de Soi", "Un site pour Oenotour Aube ?"])
    assert.deepEqual(checkSubject(s), [], s);
});

test("objet refusé : longueur, !, emoji, majuscules, mots de spam", () => {
  assert.ok(checkSubject("x".repeat(61)).length);
  assert.ok(checkSubject("").length);
  assert.ok(checkSubject("Votre site !").length);
  assert.ok(checkSubject("Votre site 🚀").length);
  assert.ok(checkSubject("Un site URGENT pour vous").length);
  assert.ok(checkSubject("Votre garage SUPER site").length);
  for (const w of ["Un site gratuit", "Notre offre", "Promo site", "Urgent : votre site", "Une offre exclusive", "Site à 100 €", "Remise de 20 %"])
    assert.ok(checkSubject(w).length, w);
});

test("objet : un mot en capitales est toléré s'il est dans le nom de l'entreprise", () => {
  assert.deepEqual(checkSubject("Un site pour BIROST", "BIROST SARL"), []);
  assert.ok(checkSubject("Un site pour BIROST", "Garage Exemple").length);
  assert.deepEqual(checkSubject("Un site pour AUTO ?"), []);   // moins de 5 lettres : toléré
});
