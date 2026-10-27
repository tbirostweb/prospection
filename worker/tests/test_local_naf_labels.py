"""Métier d'après le code NAF : formats, intitulé officiel, repli sur la division, exclusions inchangées."""
import pytest

from worker.local import naf


@pytest.mark.parametrize("raw,expected", [("43.22A", "43.22A"), ("4322A", "43.22A"), ("4322a", "43.22A"), (" 43.22 a ", "43.22A"), ("43.22", None),
                                          ("", None), (None, None), ("ABCDE", None)])
def test_normalisation_du_code(raw, expected):
    assert naf.normalize_code(raw) == expected


@pytest.mark.parametrize("code,label", [("43.22A", "Plombiers"), ("4322A", "Plombiers"), ("96.02A", "Salons de coiffure"), ("56.10A", "Restaurants")])
def test_code_du_catalogue_avec_ou_sans_point(code, label):
    key, lab, _web = naf.activity_of(code)
    assert key and lab == naf.CATALOG[key]["label"] or lab == label


@pytest.mark.parametrize("code,label", [("43.29A", "Isolation"), ("43.31Z", "Plâtriers"), ("4333Z", "Revêtements de sols et de murs"),
                                        ("95.23Z", "Cordonniers"), ("47.73Z", "Pharmacies"), ("86.21Z", "Médecins généralistes")])
def test_hors_catalogue_intitule_officiel_abrege(code, label):
    assert naf.activity_of(code) == (None, label, 0.5)


def test_sous_classe_inconnue_prend_la_division():
    assert naf.official_label("43.99X") == ("Travaux de construction spécialisés", "division")
    assert naf.activity_of("43.99X")[1] == "Travaux de construction spécialisés"
    assert naf.official_label("43.31Z")[1] == "subclass"


def test_division_inconnue_ou_code_invalide_reste_inconnu():
    assert naf.activity_of("01.11Z") == (None, None, 0.5)             # agriculture : hors table, comportement inchangé
    assert naf.activity_of("n'importe quoi") == (None, None, 0.5) and naf.activity_of(None) == (None, None, 0.5)


def test_libelles_courts_sans_virgule_ni_excès():
    table = naf.official()
    assert all(len(v["court"].split(",")[0]) >= 4 for v in table["sous_classes"].values())      # le message et la file gardent la 1re partie
    assert max(len(v["court"]) for v in table["sous_classes"].values()) <= 55
    assert all(c[:2] in table["divisions"] for c in table["sous_classes"])


@pytest.mark.parametrize("code,name,excluded", [("62.01Z", "X", True), ("6201Z", "X", True), ("73.11Z", "X", True), ("43.22A", "X", False),
                                                ("4520A", "STATION DE LAVAGE DU PARC", True), ("45.20A", "GARAGE DUPONT", False)])
def test_exclusions_inchangees_avec_ou_sans_point(code, name, excluded):
    assert bool(naf.excluded_reason(code, name)) is excluded
