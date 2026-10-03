"""Prospection locale de bout en bout sur un VRAI MySQL : SIRENE (simulé), BODACC (simulé), recherche et sites (faux réseau), audit, score."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # tests/local_fixtures.py
from local_fixtures import FakeSearch, FakeWeb, html  # noqa: E402

from worker import db  # noqa: E402
from worker.local import http, runner, sitefinder  # noqa: E402
from worker.local.sitefinder import Hit  # noqa: E402


def etab(siret, naf="56.10A", state="A", lat="48.29", lon="4.07", diff="O", created="2019-01-01", cp="10000", city="TROYES", street="1 RUE ZOLA"):
    return {"siret": siret, "adresse": f"{street} {cp} {city}", "commune": "10387", "code_postal": cp, "libelle_commune": city, "departement": "10",
            "latitude": lat, "longitude": lon, "date_creation": created, "etat_administratif": state, "activite_principale": naf,
            "liste_enseignes": None, "nom_commercial": None, "est_siege": True, "statut_diffusion_etablissement": diff}


def unit(siren, name, etabs, size="PME", emp="02", opened=1):
    return {"siren": siren, "nom_complet": name, "nature_juridique": "5710", "categorie_entreprise": size, "tranche_effectif_salarie": emp,
            "nombre_etablissements_ouverts": opened, "siege": etab(siren + "00011"), "matching_etablissements": etabs}


def sirene_units():
    return [
        unit("111111111", "LE BISTROT MARTIN", [etab("11111111100011", created="2026-07-01", street="12 RUE EMILE ZOLA")]),          # bon site
        unit("222222222", "BOULANGERIE DUPONT", [etab("22222222200011", naf="10.71C", lat="48.292", lon="4.08", street="5 RUE VOLTAIRE")]),   # sans site
        unit("333333333", "BURGER KING FRANCE", [etab("33333333300011")], size="GE", emp="41", opened=400),                    # chaîne
        unit("444444444", "AGENCE WEB DU CENTRE", [etab("44444444400011")]),                                                  # à exclure
        unit("555555555", "ANCIEN RESTO", [etab("55555555500011", state="F")]),                                               # fermé
        unit("666666666", "CHEZ DNC", [etab("66666666600011")]),                                                              # ne plus contacter
        unit("111111111", "LE BISTROT MARTIN (doublon)", [etab("11111111100011", created="2026-07-01", street="12 RUE EMILE ZOLA")]),   # SIRET dupliqué
        unit("777777777", "SALON JOLIE", [etab("77777777700011", naf="56.10C", lat="48.30", lon="4.09", street="8 RUE DES ORMES")]),       # site à moderniser
        unit("888888888", "LA TABLE RONDE", [etab("88888888800011", street="2 PLACE DU MARCHE")]),                             # site d'un homonyme
    ]


def bodacc_creations():
    return {"results": [{"dateparution": "2026-07-10", "familleavis_lib": "Créations", "registre": ["111111111", "111 111 111"],
                         "acte": json.dumps({"dateImmatriculation": "2026-07-01", "creation": {"categorieCreation": "Immatriculation"}})}]}


def install_api(monkeypatch):
    def handler(req):
        if req.url.host == "recherche-entreprises.api.gouv.fr":
            page = int(req.url.params["page"])
            return httpx.Response(200, json={"results": sirene_units() if page == 1 else [], "total_pages": 1})
        if req.url.host == "bodacc-datadila.opendatasoft.com":
            where = req.url.params["where"]
            assert 'registre="111111111"' in where and "Créations" in where          # requête ciblée par SIREN, pas départementale
            return httpx.Response(200, json=bodacc_creations())
        raise AssertionError(f"appel inattendu : {req.url}")
    monkeypatch.setattr(http, "_transport", httpx.MockTransport(handler))


def web_and_search():
    web = FakeWeb()
    legal = html(title="Mentions légales", body="<p>LE BISTROT MARTIN - SIRET 111 111 111 00011 - 12 rue Emile Zola 10000 Troyes - 03 25 11 22 33 - contact@bistrot-martin.fr</p>")
    web.add_site("www.bistrot-martin.fr", html(title="Le Bistrot Martin - Troyes", h1="Le Bistrot Martin", jsonld=None, body='<a href="/mentions-legales">Mentions légales</a><p>12 rue Emile Zola 10000 Troyes</p>'), legal=legal)
    old = html(title="Salon Jolie Troyes", h1="Salon Jolie", viewport=False, desc=None, canonical=False, og=False, body='<a href="/mentions-legales">Mentions légales</a><marquee>Promo</marquee>')
    web.add_site("salon-jolie.fr", old, legal=html(title="Mentions", body="<p>SALON JOLIE - SIRET 777 777 777 00011 - 8 rue des Ormes 10000 Troyes - contact@salon-jolie.fr</p>"),
                 https=False, sitemap=None)
    web.add_site("la-table-ronde.fr", html(title="La Table Ronde - Nancy", h1="La Table Ronde", body="<p>La Table Ronde, 4 rue Stanislas 54000 Nancy</p>"))
    search = FakeSearch({
        '"LE BISTROT MARTIN" "TROYES"': [Hit("https://www.bistrot-martin.fr/", "Le Bistrot Martin - Troyes", "restaurant à Troyes"),
                                         Hit("https://www.pagesjaunes.fr/pros/1", "Le Bistrot Martin Troyes", "Troyes")],
        '"SALON JOLIE" "TROYES"': [Hit("http://salon-jolie.fr/", "Salon Jolie Troyes", "restaurant à Troyes")],
        '"LA TABLE RONDE" "TROYES"': [Hit("https://la-table-ronde.fr/", "La Table Ronde - Nancy", "Nancy")],
        '"BOULANGERIE DUPONT" "TROYES"': [Hit("https://www.pagesjaunes.fr/pros/2", "Boulangerie Dupont Troyes", "Troyes"),
                                          Hit("https://www.facebook.com/boulangeriedupont", "Boulangerie Dupont | Facebook", "Troyes")],
    })
    return web, search


def add_campaign(conn, **over):
    row = dict(name="Troyes 10 km", city="Troyes", postal_code="10000", department="10", commune_code="10387", latitude=48.2924, longitude=4.0761,
               radius_km=10, activities=json.dumps(["restaurants", "boulangeries"]), max_companies=100, status="queued", **over)
    cid = db.execute(conn, f"INSERT INTO local_campaigns ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})", tuple(row.values()))
    conn.commit()
    return db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (cid,))


def prospect(conn, siret):
    return db.fetch_one(conn, "SELECT * FROM local_prospects WHERE siret=%s", (siret,))


def test_campagne_de_bout_en_bout(env, conn, monkeypatch):
    monkeypatch.setattr(runner, "AUDIT", True)                              # audit passif activé (LOCAL_AUDIT=1) : désactivé par défaut
    install_api(monkeypatch)
    web, search = web_and_search()
    db.execute(conn, "INSERT INTO local_do_not_contact (siret, reason) VALUES ('66666666600011', 'a demandé à ne plus être contactée')")
    camp = add_campaign(conn)
    stats = runner.run_campaign(conn, camp, search=search, fetch=web)

    rows = {r["siret"]: r for r in db.fetch_all(conn, "SELECT * FROM local_prospects")}
    assert set(rows) == {"11111111100011", "22222222200011", "66666666600011", "77777777700011", "88888888800011"}
    assert "55555555500011" not in rows                                     # fermé : ignoré ; SIRET dupliqué : UN seul prospect
    assert stats["banned"] == 2                                             # chaîne (Burger King) et agence web : bannies dès la découverte
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospect_campaigns")["n"] == 5

    a = rows["11111111100011"]                                              # site officiel trouvé, audité, contact, entreprise récente
    assert (a["website_status"], a["website_url"]) == ("CONFIRMED", "https://www.bistrot-martin.fr/") and float(a["website_confidence"]) >= 0.8
    assert a["audited_at"] and a["email"] == "contact@bistrot-martin.fr" and a["email_kind"] == "GENERIC_BUSINESS" and a["phone"] == "0325112233"
    assert a["contact_source_url"].startswith("https://www.bistrot-martin.fr/") and a["contact_discovered_at"]
    assert a["bodacc"] is None and a["status"] in ("AUDITED", "QUALIFIED")  # BODACC n'est plus interrogé par le runner (veille seulement)
    sources = {r["source"] for r in db.fetch_all(conn, "SELECT source FROM local_prospect_sources WHERE prospect_id=%s", (a["id"],))}
    assert sources == {"sirene", "websearch"}                               # sources fusionnées sur un seul prospect
    assert a["pipeline_stage"] == "DONE" and a["deep_enriched_at"] is not None   # fiche traitée = prête (plus d'enrichissement approfondi)

    b = rows["22222222200011"]                                              # « Site non trouvé » (annuaire et Facebook écartés)
    assert b["website_status"] == "NOT_FOUND" and b["website_url"] is None and b["prospect_score"] <= 61     # sans contact : au mieux « À examiner »
    assert b["website_search_tried"] >= 2 and b["website_search_total"] >= b["website_search_tried"] and 0 < float(b["website_absence_confidence"]) <= 0.85
    ev = json.loads(b["website_evidence"])
    assert ev["socials"] == ["https://www.facebook.com/boulangeriedupont"] and "aucun résultat pertinent" in ev["evidence"][0]["label"]

    g = rows["66666666600011"]
    assert (g["do_not_contact"], g["status"], g["category"]) == (1, "DO_NOT_CONTACT", "IGNORER") and g["website_status"] is None

    j = rows["77777777700011"]                                              # site ancien → refonte potentielle, brouillon
    assert j["website_status"] == "CONFIRMED" and j["modernization_opportunity"] == "HIGH" and j["prospect_score"] >= 62
    assert j["lighthouse_performance"] is None and j["lighthouse_at"] is None   # Lighthouse n'est plus lancé par le runner
    assert {i["code"] for i in json.loads(j["issues"])} >= {"no_viewport", "no_https", "meta_description_missing"}
    assert j["draft_message"] is None                                    # plus de brouillon automatique : il est créé sur demande depuis la fiche

    h = rows["88888888800011"]                                              # site d'un homonyme (Nancy) : JAMAIS rattaché
    assert h["website_status"] in ("UNCERTAIN", "NOT_FOUND") and h["website_url"] is None

    assert stats["confirmed"] + stats.get("probable", 0) == 2 and stats["not_found"] >= 1 and stats["audited"] == 2 and stats["remaining"] == 0 and "bodacc_signals" not in stats
    assert db.fetch_one(conn, "SELECT status FROM local_campaigns WHERE id=%s", (camp["id"],))["status"] == "done"
    details = json.loads(a["score_details"])["details"]
    sd = json.loads(a["score_details"])
    assert abs(sum(x["points"] for x in details if x["key"] != "cap") - sd["raw"]) <= 1 and all(x["detail"] for x in details)   # score explicable (brut → plafonds → final)
    assert a["prospect_score"] == min([sd["raw"]] + [c["limit"] for c in sd["caps"]]) and a["data_confidence_score"] is not None


def test_relance_idempotente_et_statut_manuel_respecte(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=web)
    before = db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects")["n"]
    pid = prospect(conn, "22222222200011")["id"]
    db.execute(conn, "UPDATE local_prospects SET status='CONTACTED', contacted_at=UTC_TIMESTAMP(), notes='appelé le 21/09' WHERE id=%s", (pid,))
    conn.commit()
    search.queries.clear()
    web.calls.clear()
    fresh = db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp["id"],))
    db.execute(conn, "UPDATE local_campaigns SET status='queued', stats=%s WHERE id=%s", (json.dumps({}), camp["id"]))      # « Relancer » : redécouverte
    conn.commit()
    runner.run_campaign(conn, db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp["id"],)), search=search, fetch=web)
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects")["n"] == before          # aucun doublon
    p = db.fetch_one(conn, "SELECT status, notes FROM local_prospects WHERE id=%s", (pid,))
    assert (p["status"], p["notes"]) == ("CONTACTED", "appelé le 21/09")                              # le worker n'écrase jamais le suivi manuel
    assert search.queries == []                                                                       # rien n'est recherché deux fois


def test_recherche_en_panne_n_est_jamais_prise_pour_aucun_site(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, _ = web_and_search()

    def broken(query):
        raise runner.SearchUnavailable("moteurs indisponibles : google (Suspended: CAPTCHA)")

    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=broken, fetch=web)
    statuses = {r["website_status"] for r in db.fetch_all(conn, "SELECT website_status FROM local_prospects")}
    assert statuses == {None}                                                                          # pas un seul « NOT_FOUND »
    c = db.fetch_one(conn, "SELECT status, last_error FROM local_campaigns WHERE id=%s", (camp["id"],))
    assert c["status"] == "running" and "CAPTCHA" in c["last_error"]        # en attente (visible), pas en erreur : les moteurs reviendront


def test_audit_desactive_par_defaut_site_et_contacts_seulement_sans_retraitement(env, conn, monkeypatch):
    """LOCAL_AUDIT=0 (défaut) : site et contacts seulement ; la fiche est terminée et n'est jamais retraitée en boucle (audited_at reste NULL)."""
    assert runner.AUDIT is False
    install_api(monkeypatch)
    web, search = web_and_search()
    camp = add_campaign(conn)
    stats = runner.run_campaign(conn, camp, search=search, fetch=web)
    a = prospect(conn, "11111111100011")
    assert a["website_status"] == "CONFIRMED" and a["email"] == "contact@bistrot-martin.fr" and a["phone"] == "0325112233"
    assert a["audited_at"] is None and a["modernization_opportunity"] is None and a["issues"] is None and "audited" not in stats
    assert a["pipeline_stage"] == "DONE" and a["deep_enriched_at"] is not None
    assert stats["remaining"] == 0 and db.fetch_one(conn, "SELECT status FROM local_campaigns WHERE id=%s", (camp["id"],))["status"] == "done"
    search.queries.clear()
    web.calls.clear()
    stats = runner.run_campaign(conn, db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp["id"],)), search=search, fetch=web)
    assert search.queries == [] and web.calls == [] and stats["remaining"] == 0                  # rien n'est retraité


def test_fiches_deja_traitees_sans_deep_enriched_at_deviennent_pretes(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=web)
    db.execute(conn, "UPDATE local_prospects SET deep_enriched_at=NULL")                     # fiches d'avant la suppression de l'approfondissement
    conn.commit()
    runner.run_campaign(conn, db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp["id"],)), search=search, fetch=web)
    n = db.fetch_one(conn, "SELECT SUM(deep_enriched_at IS NULL) AS missing, COUNT(*) AS n FROM local_prospects WHERE pipeline_stage='DONE'")
    assert n["n"] > 0 and int(n["missing"]) == 0


def test_delai_depasse_aucune_fiche_traitee_tout_est_repris(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    camp = add_campaign(conn)
    import time as _t
    stats = runner.run_campaign(conn, camp, search=search, fetch=web, deadline=_t.monotonic() - 1)
    assert stats["time_budget_hit"] is True and search.queries == []
    assert db.fetch_one(conn, "SELECT status FROM local_campaigns WHERE id=%s", (camp["id"],))["status"] == "running"
    stats = runner.run_campaign(conn, db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp["id"],)), search=search, fetch=web)
    assert "time_budget_hit" not in stats and stats["remaining"] == 0 and prospect(conn, "11111111100011")["website_status"] == "CONFIRMED"


def test_geocodage_de_la_campagne_par_l_api_officielle(env, conn, monkeypatch):
    def handler(req):
        if req.url.host == "geo.api.gouv.fr":
            return httpx.Response(200, json=[{"nom": "Troyes", "code": "10387", "codesPostaux": ["10000"], "centre": {"coordinates": [4.0761, 48.2924]}, "departement": {"code": "10"}}])
        if req.url.host == "recherche-entreprises.api.gouv.fr":
            assert req.url.params["lat"] == "48.2924" and req.url.params["long"] == "4.0761" and req.url.params["radius"] == "10.0"
            return httpx.Response(200, json={"results": [], "total_pages": 1})
        return httpx.Response(200, json={"results": []})
    monkeypatch.setattr(http, "_transport", httpx.MockTransport(handler))
    web, search = web_and_search()
    camp = add_campaign(conn, **{})
    db.execute(conn, "UPDATE local_campaigns SET latitude=NULL, longitude=NULL, department=NULL WHERE id=%s", (camp["id"],))
    conn.commit()
    runner.run_campaign(conn, db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp["id"],)), search=search, fetch=web)
    c = db.fetch_one(conn, "SELECT latitude, longitude, department, status FROM local_campaigns WHERE id=%s", (camp["id"],))
    assert (float(c["latitude"]), c["department"], c["status"]) == (48.2924, "10", "done")


def test_contraintes_mysql_de_la_prospection_locale(env, conn):
    import pymysql
    with pytest.raises(pymysql.err.OperationalError):
        db.execute(conn, "INSERT INTO local_prospects (fingerprint, company_name, status) VALUES ('x','A','EN_VOL')")             # statut hors liste
    with pytest.raises(pymysql.err.OperationalError):
        db.execute(conn, "INSERT INTO local_prospects (fingerprint, company_name, website_status) VALUES ('y','A','PEUT_ETRE')")
    db.execute(conn, "INSERT INTO local_prospects (siret, fingerprint, company_name) VALUES ('12345678900011','f1','A')")
    with pytest.raises(pymysql.err.IntegrityError):
        db.execute(conn, "INSERT INTO local_prospects (siret, fingerprint, company_name) VALUES ('12345678900011','f2','B')")     # SIRET unique
    with pytest.raises(pymysql.err.IntegrityError):
        db.execute(conn, "INSERT INTO local_prospects (siret, fingerprint, company_name) VALUES (NULL,'f1','C')")                  # empreinte unique
