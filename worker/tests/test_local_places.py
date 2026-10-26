"""Google Places : rapprochement d'un résultat avec le prospect (aucun appel réseau)."""
import pytest

from worker.local import places

DUPONT = dict(company_name="BOULANGERIE DUPONT", trade_name=None, address="5 RUE VOLTAIRE 10000 TROYES", postal_code="10000", city="TROYES",
              latitude=48.292, longitude=4.08)


def place(name="Boulangerie Dupont", address="5 Rue Voltaire, 10000 Troyes, France", site="https://www.pains-dupont.fr/", status="OPERATIONAL",
          loc=(48.2921, 4.0801), pid="ChIJdupont"):
    return {"id": pid, "displayName": {"text": name, "languageCode": "fr"}, "formattedAddress": address, "websiteUri": site, "businessStatus": status,
            "location": {"latitude": loc[0], "longitude": loc[1]}}


def test_bon_resultat_retenu_avec_son_site():
    pid, url, had = places.pick(DUPONT, [place()])
    assert (pid, url, had) == ("ChIJdupont", "https://www.pains-dupont.fr/", True)


def test_url_reduite_a_l_origine_sans_parametres_de_suivi():
    assert places.pick(DUPONT, [place(site="https://www.pains-dupont.fr/accueil?utm_source=google#x")])[1] == "https://www.pains-dupont.fr/"


def test_homonyme_d_une_autre_ville_ignore():
    other = place(address="12 Rue de la Paix, 54000 Nancy, France", loc=(48.69, 6.18), site="https://dupont-nancy.fr/")
    assert places.pick(DUPONT, [other]) == (None, None, False)


def test_meme_ville_mais_trop_loin_ignore():
    far = place(loc=(48.40, 4.30))                         # même adresse texte, mais ~20 km : autre établissement
    assert places.pick(DUPONT, [far])[0] is None


def test_autre_nom_ignore():
    assert places.pick(DUPONT, [place(name="Boulangerie Martin")])[0] is None


@pytest.mark.parametrize("site", ["https://www.facebook.com/boulangeriedupont", "https://instagram.com/dupont", "https://www.pagesjaunes.fr/pros/1",
                                  "https://maps.google.com/?cid=1", "https://dupont.business.site/", "ftp://x.fr/", "", None])
def test_site_reseau_social_annuaire_ou_google_ignore(site):
    pid, url, _had = places.pick(DUPONT, [place(site=site)])
    assert pid == "ChIJdupont" and url is None             # la fiche correspond, mais son « site » n'est pas un site


def test_ferme_definitivement_ignore():
    assert places.pick(DUPONT, [place(status="CLOSED_PERMANENTLY")]) == (None, None, False)
    assert places.pick(DUPONT, [place(status="CLOSED_TEMPORARILY")])[0] == "ChIJdupont"


def test_deuxieme_resultat_retenu_si_le_premier_ne_correspond_pas():
    assert places.pick(DUPONT, [place(name="Autre", pid="a"), place(pid="b")])[0] == "b"


def test_corps_de_la_requete():
    b = places.build_body(DUPONT)
    assert b["textQuery"] == "BOULANGERIE DUPONT 5 RUE VOLTAIRE 10000 TROYES" and b["languageCode"] == "fr" and b["regionCode"] == "FR"
    assert b["maxResultCount"] == 3 and b["locationBias"]["circle"]["radius"] == 5000.0
    assert "locationBias" not in places.build_body({**DUPONT, "latitude": None, "longitude": None})
