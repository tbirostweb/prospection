"""Coordonnées lues dans les extraits de recherche : attribution stricte (nom + lieu), zone téléphonique, pas de numéro surtaxé, pas d'adresse d'annuaire."""
from worker.local import snippets
from worker.local.sitefinder import Hit

DUPONT = dict(company_name="BOULANGERIE DUPONT", trade_name=None, city="TROYES", postal_code="10000", department="10")


def pj(content, title="Boulangerie Dupont - Troyes", url="https://www.pagesjaunes.fr/pros/123"):
    return Hit(url, title, content)


def test_telephone_et_email_attribues_depuis_un_extrait_d_annuaire():
    c = snippets.extract(DUPONT, [pj("Boulangerie Dupont, 5 rue Voltaire 10000 Troyes. Tél : 03 25 00 11 22 — boulangerie.dupont@gmail.com")])
    assert c["phone"] == "0325001122" and c["email"] == "boulangerie.dupont@gmail.com" and c["email_kind"] == "UNCERTAIN"
    assert c["evidence"][0]["via"] == "search_snippet" and "pagesjaunes.fr" in c["evidence"][0]["source"]
    assert c["contact_confidence"] <= snippets.MAX_CONF and c["phone_confidence"] < 0.6          # jamais assez pour « Très bon »


def test_extrait_sans_le_nom_ou_sans_le_lieu_est_ignore():
    assert snippets.extract(DUPONT, [pj("Boucherie Martin 10000 Troyes 03 25 99 88 77", title="Boucherie Martin")])["phone"] is None   # autre entreprise
    assert snippets.extract(DUPONT, [pj("Boulangerie Dupont à Lyon 03 25 00 11 22", title="Boulangerie Dupont")])["phone"] is None     # pas notre ville


def test_zone_telephonique_et_numeros_surtaxes():
    assert snippets.phone_plausible("0325001122", "10") and snippets.phone_plausible("0612345678", "10") and snippets.phone_plausible("0912345678", "10")
    assert not snippets.phone_plausible("0472001122", "10")          # 04 = Sud-Est, pas l'Aube
    assert not snippets.phone_plausible("0899123456", "10")          # numéro surtaxé de mise en relation d'annuaire
    assert snippets.phone_plausible("0472001122", None)              # département inconnu : on ne rejette pas sur la zone
    c = snippets.extract(DUPONT, [pj("Boulangerie Dupont Troyes 10000 : 08 99 12 34 56 (mise en relation)")])
    assert c["phone"] is None


def test_numeros_contradictoires_le_plus_cite_gagne_egalite_aucun():
    tie = snippets.extract(DUPONT, [pj("Boulangerie Dupont Troyes 03 25 00 11 22"), pj("Boulangerie Dupont Troyes 03 25 99 99 98", url="https://www.118712.fr/x")])
    assert tie["phone"] is None
    maj = snippets.extract(DUPONT, [pj("Boulangerie Dupont Troyes 03 25 00 11 22"), pj("Boulangerie Dupont Troyes 03 25 00 11 22", url="https://www.118712.fr/x"),
                                    pj("Boulangerie Dupont Troyes 03 25 99 99 98", url="https://annuaire.fr/y")])
    assert maj["phone"] == "0325001122" and maj["evidence"][0]["conflicts"] == 1 and set(maj["evidence"][0]["sources"]) == {"pagesjaunes.fr", "118712.fr"}


def test_jamais_l_adresse_de_l_annuaire_ni_une_adresse_sans_rapport():
    c = snippets.extract(DUPONT, [pj("Boulangerie Dupont Troyes — contact@pagesjaunes.fr — commercial@autre-societe.fr — info@boulangerie-dupont.fr")])
    assert c["email"] == "info@boulangerie-dupont.fr" and c["email_kind"] == "GENERIC_BUSINESS"


def test_requete_dediee_aux_coordonnees():
    assert snippets.contact_query(DUPONT) == '"BOULANGERIE DUPONT" TROYES téléphone'
    assert snippets.contact_query({"company_name": "X"}) is None
