"""SIRENE, BODACC, géocodage, client HTTP poli : formats réels (relevés le 21/09/2026) rejoués sans réseau."""
import json
from datetime import date

import httpx
import pytest

from worker.local import bodacc, http, sirene, zone

TROYES = (48.2924, 4.0761)


def etab(siret, naf="56.10A", state="A", lat="48.29", lon="4.07", diff="O", created="2020-01-01", enseignes=None, siege=False):
    return {"siret": siret, "adresse": "1 RUE X 10000 TROYES", "commune": "10387", "code_postal": "10000", "libelle_commune": "TROYES", "departement": "10",
            "latitude": lat, "longitude": lon, "date_creation": created, "etat_administratif": state, "activite_principale": naf,
            "liste_enseignes": enseignes, "nom_commercial": None, "est_siege": siege, "statut_diffusion_etablissement": diff}


def unit(siren, name, etabs, size="PME", emp="02", opened=1, nj="5710"):
    return {"siren": siren, "nom_complet": name, "nature_juridique": nj, "categorie_entreprise": size, "tranche_effectif_salarie": emp,
            "nombre_etablissements_ouverts": opened, "siege": etab(siren + "00011", siege=True), "matching_etablissements": etabs}


@pytest.fixture(autouse=True)
def no_sleep_transport():
    yield
    http._transport = None


def serve(handler):
    http._transport = httpx.MockTransport(handler)


def test_parse_unit_ne_garde_que_les_etablissements_actifs_diffusables_dans_le_rayon_et_le_bon_naf():
    u = unit("111111111", "LE BISTROT", [
        etab("11111111100011"),                                              # ok
        etab("11111111100029", state="F"),                                   # fermé : jamais utilisé
        etab("11111111100037", diff="P"),                                    # non diffusable : jamais utilisé
        etab("11111111100045", lat="45.75", lon="4.85"),                     # Lyon : hors rayon
        etab("11111111100052", naf="47.11B"),                                # autre activité
        etab("11111111100060", enseignes=["CHEZ MARTIN"], created="2026-03-24")])
    out = sirene.parse_unit(u, TROYES, 5, ["56.10A"])
    assert [e.siret for e in out] == ["11111111100011", "11111111100060"]
    e = out[1]
    assert e.trade_name == "CHEZ MARTIN" and e.created_at == date(2026, 3, 24) and e.company_size == "PME" and e.distance_km < 3


def test_discover_pagine_dedoublonne_le_siret_et_ne_compte_que_les_independants():
    pages = {1: [unit("111111111", "BURGER KING FRANCE", [etab("11111111100011")], size="GE", emp="41", opened=400)],
             2: [unit("222222222", "BISTROT A", [etab("22222222200011")]), unit("222222222", "BISTROT A (doublon)", [etab("22222222200011")])],
             3: [unit("333333333", "BISTROT B", [etab("33333333300011")]), unit("444444444", "BISTROT C", [etab("44444444400011")])]}
    calls = []

    def handler(req):
        page = int(req.url.params["page"])
        calls.append(page)
        assert req.url.params["etat_administratif"] == "A" and req.url.params["activite_principale"] == "56.10A"
        assert "user-agent" in req.headers and "Prospection" in req.headers["user-agent"]
        return httpx.Response(200, json={"results": pages.get(page, []), "total_pages": 3, "total_results": 5})

    serve(handler)
    indep = lambda e: e.company_size not in ("ETI", "GE")                        # noqa: E731
    got = list(sirene.discover(*TROYES, 5, ["56.10A"], max_companies=2, accept=indep))
    assert [e.siret for e in got] == ["11111111100011", "22222222200011", "33333333300011"]   # la chaîne est rendue (marquée) mais ne compte pas
    assert calls == [1, 2, 3]                                                    # on descend dans les pages jusqu'à 2 indépendants


def test_discover_s_arrete_a_la_derniere_page():
    serve(lambda req: httpx.Response(200, json={"results": [unit("111111111", "A", [etab("11111111100011")])], "total_pages": 1}))
    assert len(list(sirene.discover(*TROYES, 5, ["56.10A"], max_companies=50))) == 1


def test_import_stock_csv_flux_filtre_actifs_diffusables(tmp_path):
    f = tmp_path / "stock.csv"
    f.write_text(
        "siret,siren,etatAdministratifEtablissement,statutDiffusionEtablissement,activitePrincipaleEtablissement,codePostalEtablissement,"
        "libelleCommuneEtablissement,libelleVoieEtablissement,typeVoieEtablissement,numeroVoieEtablissement,denominationUsuelleEtablissement,"
        "enseigne1Etablissement,dateCreationEtablissement,trancheEffectifsEtablissement,etablissementSiege,codeCommuneEtablissement\n"
        "11111111100011,111111111,A,O,56.10A,10000,TROYES,ZOLA,RUE,12,LE BISTROT,,2026-05-01,02,true,10387\n"
        "22222222200011,222222222,F,O,56.10A,10000,TROYES,ZOLA,RUE,14,FERME,,2010-01-01,02,true,10387\n"
        "33333333300011,333333333,A,P,56.10A,10000,TROYES,ZOLA,RUE,16,PRIVE,,2010-01-01,02,true,10387\n"
        "44444444400011,444444444,A,O,47.11B,10000,TROYES,ZOLA,RUE,18,AUTRE,,2010-01-01,02,true,10387\n"
        "55555555500011,555555555,A,O,56.10A,21000,DIJON,ZOLA,RUE,20,AILLEURS,,2010-01-01,02,true,21231\n", encoding="utf-8")
    out = list(sirene.import_stock_csv(str(f), departments=["10"], naf_codes=["56.10A"]))
    assert [e.siret for e in out] == ["11111111100011"] and out[0].source == "sirene_bulk" and out[0].created_at == date(2026, 5, 1)


def test_bodacc_creations_par_siren_avec_dates():
    def handler(req):
        assert 'familleavis_lib="Créations"' in req.url.params["where"] and 'numerodepartement="10"' in req.url.params["where"]
        return httpx.Response(200, json={"results": [
            {"dateparution": "2026-09-20", "familleavis_lib": "Créations", "registre": ["519418479", "519 418 479"],
             "acte": json.dumps({"dateImmatriculation": "2026-09-16", "dateCommencementActivite": "2026-09-14", "creation": {"categorieCreation": "Immatriculation"}})},
            {"dateparution": "2026-09-19", "familleavis_lib": "Créations", "registre": ["abc"], "acte": "{}"}]})

    serve(handler)
    c = bodacc.fetch_creations("10", date(2026, 1, 1), max_pages=1)
    assert c == {"519418479": {"kind": "creation", "published": "2026-09-20", "registered": "2026-09-16", "started": "2026-09-14", "category": "Immatriculation"}}
    assert bodacc.signal_for("519418479", c, {}) ["kind"] == "creation" and bodacc.signal_for("000000000", c, {}) is None


def test_geocodage_officiel_et_distance():
    def handler(req):
        assert req.url.host == "geo.api.gouv.fr"                                 # jamais Nominatim
        return httpx.Response(200, json=[{"nom": "Troyes", "code": "10387", "codesPostaux": ["10000"], "centre": {"coordinates": [4.0761, 48.2924]},
                                          "departement": {"code": "10"}, "population": 62088}])

    serve(handler)
    z = zone.geocode_commune("Troyes")
    assert (z["commune_code"], z["department"], z["postal_code"]) == ("10387", "10", "10000")
    assert 9.0 < zone.haversine_km(48.2924, 4.0761, 48.3, 4.2) < 9.5 and zone.haversine_km(1, 1, 1, 1) == 0
    serve(lambda req: httpx.Response(200, json=[]))
    assert zone.geocode_commune("Nulle-Part-Sur-Loire") is None


def test_client_http_une_reprise_sur_429_puis_abandon_sans_contournement():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "1"})

    serve(handler)
    with pytest.raises(http.ApiError):
        http.get_json("https://recherche-entreprises.api.gouv.fr/near_point", {"a": 1})
    assert len(calls) == 2                                                       # une seule nouvelle tentative, on ne martèle pas l'API

    seq = iter([httpx.Response(503), httpx.Response(200, json={"ok": True})])
    serve(lambda req: next(seq))
    assert http.get_json("https://geo.api.gouv.fr/x") == {"ok": True}
    serve(lambda req: httpx.Response(404))
    with pytest.raises(http.ApiError):
        http.get_json("https://geo.api.gouv.fr/x")


def test_bodacc_lookup_par_siren_en_lots_creation_prioritaire():
    seen = []

    def handler(req):
        where = req.url.params["where"]
        seen.append(where)
        return httpx.Response(200, json={"results": [
            {"dateparution": "2026-03-01", "familleavis_lib": "Ventes et cessions", "registre": ["111111111"], "acte": "{}"},
            {"dateparution": "2026-02-01", "familleavis_lib": "Créations", "registre": ["111 111 111", "111111111"],
             "acte": json.dumps({"dateImmatriculation": "2026-01-30", "creation": {"categorieCreation": "Immatriculation"}})},
            {"dateparution": "2026-04-01", "familleavis_lib": "Modifications diverses", "registre": ["222222222"], "acte": "{}"},
            {"dateparution": "2026-04-02", "familleavis_lib": "Créations", "registre": ["999999999"], "acte": "{}"}]})   # SIREN non demandé : ignoré

    serve(handler)
    sirens = [f"{i:09d}" for i in range(111111111, 111111111 + 1)] + ["222222222", "abc", "", "111111111"]
    got = bodacc.lookup(sirens, date(2025, 1, 1))
    assert got["111111111"]["kind"] == "creation" and got["111111111"]["registered"] == "2026-01-30"      # la création l'emporte
    assert got["222222222"] == {"kind": "change", "published": "2026-04-01", "family": "Modifications diverses",
                                "events": [{"kind": "change", "published": "2026-04-01"}]}
    assert [e["kind"] for e in got["111111111"]["events"]] == ["takeover", "creation"]                    # la reprise reste visible
    assert "999999999" not in got and len(seen) == 1                                                        # un seul lot, valeurs invalides écartées
    assert 'registre="111111111"' in seen[0] and 'registre="222222222"' in seen[0] and "abc" not in seen[0]


# Annonces réelles (Aube, relevées le 26/09/2026), réduites aux champs utiles.
REAL_TAKEOVER = {"familleavis_lib": "Ventes et cessions", "registre": ["849624911", "849 624 911", "930673801", "930 673 801"],
                 "acte": json.dumps({"descriptif": "transfert de l'établissement principal. Cession sous acte authentique",
                                     "vente": {"categorieVente": "Achat d'un établissement secondaire ou complémentaire"}}),
                 "listeetablissements": json.dumps({"etablissement": {"origineFonds": "établissement principal acquis par achat au prix stipulé de 20000.00 euros"}})}


@pytest.mark.parametrize("rec,kind", [
    (REAL_TAKEOVER, "takeover"),
    ({"familleavis_lib": "Modifications diverses", "modificationsgenerales": json.dumps({"descriptif": "Modification survenue sur le capital, transfert du siège social."})}, "move"),
    ({"familleavis_lib": "Modifications diverses", "modificationsgenerales": json.dumps({"descriptif": "Modification survenue sur l'administration, cessation d'activité, dissolution de la société."})}, "closure"),
    ({"familleavis_lib": "Modifications diverses", "modificationsgenerales": json.dumps({"descriptif": "Modification survenue sur la dénomination."})}, "rename"),
    ({"familleavis_lib": "Modifications diverses", "modificationsgenerales": json.dumps({"descriptif": "Modification survenue sur l'administration."})}, "change"),
    ({"familleavis_lib": "Créations", "acte": json.dumps({"descriptif": "Immatriculation d'une personne physique suite à achat du fonds"})}, "takeover"),
    ({"familleavis_lib": "Créations", "acte": json.dumps({"creation": {"categorieCreation": "Immatriculation d'une personne morale"}})}, "creation"),
    ({"familleavis_lib": "Radiations"}, "radiation")])
def test_bodacc_nature_des_annonces(rec, kind):
    assert bodacc.classify(rec) == kind


def test_bodacc_reprise_attribuee_a_l_acheteur_jamais_au_vendeur():
    serve(lambda req: httpx.Response(200, json={"results": [{**REAL_TAKEOVER, "dateparution": "2026-09-20"}]}))
    got = bodacc.lookup(["849624911", "930673801"], date(2026, 1, 1))
    assert got["849624911"]["events"] == [{"kind": "takeover", "published": "2026-09-20"}] and "930673801" not in got


def test_sirene_chiffre_d_affaires_et_dirigeant():
    unit = {"siren": "849624911", "nom_complet": "LAURE CRAME", "finances": {"2023": {"ca": 90000}, "2024": {"ca": 120000}},
            "dirigeants": [{"nom": "CRAME", "prenoms": "LAURE ANNIE FRANCOISE", "annee_de_naissance": "1998", "type_dirigeant": "personne physique"}],
            "matching_etablissements": [{"siret": "84962491100010", "etat_administratif": "A", "activite_principale": "96.02A", "latitude": "48.3", "longitude": "4.07"}]}
    e = sirene.parse_unit(unit)[0]
    assert (e.revenue, e.revenue_year, e.manager_name) == (120000, 2024, "Laure Crame")                  # jamais la date de naissance
    assert sirene.manager({"dirigeants": [{"nom": "X SAS", "type_dirigeant": "personne morale"}]}) is None


def test_departement_departage_les_homonymes_et_suffit_seul():
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json=[{"nom": "Saint-Denis", "code": "93066", "codesPostaux": ["93200"], "centre": {"coordinates": [2.35, 48.93]},
                                          "departement": {"code": "93"}, "population": 110000}])
    serve(handler)
    z = zone.geocode_commune("Saint-Denis", department="93")
    assert seen[-1]["codeDepartement"] == "93" and not z["ambiguous"] and z["department"] == "93"
    z = zone.geocode_commune(department="93")
    assert "nom" not in seen[-1] and seen[-1]["boost"] == "population" and z["commune_code"] == "93066"   # centre = la plus peuplée
