// Métier court pour le message et la file /rediger : un intitulé long ne produit jamais de phrase bancale.
import { test } from "node:test";
import assert from "node:assert/strict";
import { searchedAs, shortActivity } from "../lib/activity";

test("libellés courts du worker : gardés tels quels, 1re partie avant la virgule", () => {
  assert.equal(shortActivity("Plombiers"), "Plombiers");
  assert.equal(shortActivity("Boulangeries, pâtisseries"), "Boulangeries");
  assert.equal(shortActivity(null), null);
  assert.equal(shortActivity("  "), null);
});

test("intitulé officiel long : coupé avant « et » / « ou » / parenthèse, jamais plus de 40 caractères", () => {
  const long = "Commerce de détail de quincaillerie, peintures et verres en petites surfaces (moins de 400 m²)";
  assert.equal(shortActivity(long), "Commerce de détail de quincaillerie");
  assert.equal(shortActivity("Installation de structures métalliques chaudronnées et de tuyauterie"), "Installation de structures métalliques");
  const noCut = shortActivity("Fabrication industrielle de produits alimentaires divers non classés");
  assert.ok(noCut && noCut.length <= 40 && !noCut.endsWith(" "));
});

test("ce que le client tape sur Google : pluriel avec « des », nom d'activité tel quel", () => {
  assert.equal(searchedAs("Plombiers"), "des plombiers");
  assert.equal(searchedAs("Isolation"), "isolation");
  assert.equal(searchedAs(null), null);
});
