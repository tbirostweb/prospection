"""Google Places sur un VRAI MySQL : le site suggéré est VÉRIFIÉ par le pipeline (SIRET dans les mentions légales), e-mail et téléphone lus sur
le site ; seul le place_id est conservé ; plafond mensuel persistant ; sans clé, aucun appel. Google simulé (aucun appel réel)."""
from __future__ import annotations

import json

import httpx
import pytest
from local_fixtures import html

from worker import db
from worker.local import places, runner, siterecheck

from .test_local_pipeline import add_campaign, install_api, prospect, web_and_search

DUPONT = "22222222200011"
SITE = "pains-d-antan.fr"
LEGAL = html(title="Mentions légales", h1="Mentions légales",
             body="<p>BOULANGERIE DUPONT - SIRET 222 222 222 00011 - 5 rue Voltaire 10000 Troyes - 03 25 40 50 60 - contact@pains-d-antan.fr</p>")


@pytest.fixture(autouse=True)
def _places_isole(env, conn, monkeypatch):
    monkeypatch.setattr(places, "_blocked", False)
    places._session.clear()
    db.execute(conn, "DELETE FROM local_places_usage")      # compteur mensuel : propre à chaque test
    yield
    places._transport = None
    places._session.clear()


def install_places(monkeypatch, site=f"https://www.{SITE}/?utm_source=gmb", key="cle-test", budget=900, status=200):
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request):
        calls.append(req)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "refusé"}})
        return httpx.Response(200, json={"places": [{
            "id": "ChIJdupont", "displayName": {"text": "Boulangerie Dupont", "languageCode": "fr"}, "formattedAddress": "5 Rue Voltaire, 10000 Troyes, France",
            "websiteUri": site, "businessStatus": "OPERATIONAL", "location": {"latitude": 48.292, "longitude": 4.08},
            "nationalPhoneNumber": "03 99 99 99 99"}]})                       # téléphone Google : JAMAIS utilisé ni stocké
    monkeypatch.setattr(places, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(places, "API_KEY", key)
    monkeypatch.setattr(places, "MONTHLY_BUDGET", budget)
    return calls


def _web(web):
    web.add_site(SITE, html(title="Boulangerie Dupont - Troyes", h1="Boulangerie Dupont", canonical=False, body='<a href="/mentions-legales">Mentions légales</a>'),
                 legal=LEGAL)
    web.add_site(f"www.{SITE}", html(title="Boulangerie Dupont - Troyes", h1="Boulangerie Dupont", canonical=False, body='<a href="/mentions-legales">Mentions légales</a>'),
                 legal=LEGAL)


def _run(conn, monkeypatch, **kw):
    install_api(monkeypatch)
    calls = install_places(monkeypatch, **kw)
    web, search = web_and_search()
    _web(web)
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    return calls, web, search


def test_site_de_google_verifie_confirme_et_contacts_lus_sur_le_site(env, conn, monkeypatch):
    calls, _web_, _search = _run(conn, monkeypatch)
    p = prospect(conn, DUPONT)
    assert p["website_status"] == "CONFIRMED" and p["website_url"] == f"https://www.{SITE}/"
    assert p["email"] == "contact@pains-d-antan.fr" and p["phone"] == "0325405060"           # lus sur le site, pas chez Google
    assert "0399999999" not in json.dumps(p, default=str) and "03 99 99 99 99" not in json.dumps(p, default=str)
    ev = json.loads(p["website_evidence"])["evidence"]
    assert any(e["code"] == "siret" for e in ev) and not any(e["code"] == "structured_source" for e in ev)   # preuve = le site lui-même
    prov = json.loads(p["provenance"])["places"]
    assert set(prov) == {"checked_at", "place_id", "had_website", "site_checked_at", "site_checked"} and prov["place_id"] == "ChIJdupont"
    assert prov["had_website"] is True and prov["site_checked"] == "confirmed"                # verdict : plus jamais redemandé
    assert SITE not in json.dumps(json.loads(p["provenance"])["places"]) and "Voltaire" not in p["provenance"]   # aucune URL ni contenu Places en base
    dupont_calls = [c for c in calls if b"DUPONT" in c.content]
    assert len(dupont_calls) == 1                                                            # une seule requête pour ce prospect
    r = dupont_calls[0]
    assert r.url == "https://places.googleapis.com/v1/places:searchText" and r.headers["X-Goog-Api-Key"] == "cle-test"
    assert "places.websiteUri" in r.headers["X-Goog-FieldMask"] and "Phone" not in r.headers["X-Goog-FieldMask"]
    body = json.loads(r.content)
    assert body["maxResultCount"] == 3 and body["regionCode"] == "FR" and body["locationBias"]["circle"]["radius"] == 5000.0
    assert places.month_used(conn) == len(calls) and len(calls) >= 1


def test_pas_de_nouvelle_requete_avant_90_jours(env, conn, monkeypatch):
    calls, _w, _s = _run(conn, monkeypatch)
    row = prospect(conn, DUPONT)
    n = len(calls)
    places._session.clear()
    assert places.candidate_url(conn, row, runner._company(conn, row)) is None and len(calls) == n    # mémorisé : rien de nouveau
    db.execute(conn, "UPDATE local_prospects SET provenance=JSON_SET(provenance, '$.places.checked_at', '2026-05-01T00:00:00Z') WHERE id=%s", (row["id"],))
    row = prospect(conn, DUPONT)
    places.candidate_url(conn, row, runner._company(conn, row))
    assert len(calls) == n + 1                                                               # plus de 90 jours : une nouvelle requête


def test_plafond_mensuel_atteint_aucun_appel(env, conn, monkeypatch):
    install_api(monkeypatch)
    calls = install_places(monkeypatch, budget=2)
    db.execute(conn, "INSERT INTO local_places_usage (month, requests) VALUES (DATE_FORMAT(UTC_TIMESTAMP(), '%%Y-%%m'), 2)")
    web, search = web_and_search()
    _web(web)
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    assert calls == [] and places.month_used(conn) == 2
    p = prospect(conn, DUPONT)
    assert p["website_status"] == "NOT_FOUND" and "places" not in (p["provenance"] or "")    # rien n'est mémorisé : il sera tenté le mois suivant


def test_le_plafond_est_applique_en_cours_de_passage_et_remis_a_zero_au_mois_suivant(env, conn, monkeypatch):
    install_places(monkeypatch, budget=1)
    assert places.reserve(conn) and not places.reserve(conn) and places.month_used(conn) == 1
    db.execute(conn, "UPDATE local_places_usage SET month='2026-01'")
    assert places.month_used(conn) == 0 and places.reserve(conn) and places.month_used(conn) == 1


def test_le_plafond_du_worker_est_ecrit_pour_la_page_statistiques(env, conn, monkeypatch):
    install_places(monkeypatch, budget=7)
    assert places.reserve(conn)
    row = db.fetch_one(conn, "SELECT requests, budget FROM local_places_usage")
    assert row["requests"] == 1 and row["budget"] == 7


def test_sans_cle_ou_budget_zero_aucun_appel(env, conn, monkeypatch):
    for key, budget in (("", 900), ("cle", 0)):
        calls, _w, _s = _run(conn, monkeypatch, key=key, budget=budget)
        assert calls == [] and prospect(conn, DUPONT)["website_status"] == "NOT_FOUND"
        assert places.month_used(conn) == 0
        db.execute(conn, "DELETE FROM local_prospects")


def test_cle_refusee_requete_rendue_et_plus_aucun_appel(env, conn, monkeypatch):
    calls, _w, _s = _run(conn, monkeypatch, status=403)
    assert len(calls) == 1 and places.month_used(conn) == 0                                  # un seul essai, requête rendue, module coupé pour le passage
    assert prospect(conn, DUPONT)["website_status"] == "NOT_FOUND" and "places" not in (prospect(conn, DUPONT)["provenance"] or "")


def test_site_facebook_de_google_n_est_pas_un_candidat(env, conn, monkeypatch):
    calls, _w, _s = _run(conn, monkeypatch, site="https://www.facebook.com/boulangeriedupont")
    p = prospect(conn, DUPONT)
    assert p["website_status"] == "NOT_FOUND"
    prov = json.loads(p["provenance"])["places"]
    assert prov["had_website"] is True and prov["place_id"] == "ChIJdupont"


def test_revérification_des_fiches_sans_site_utilise_places(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)                   # Places inactif : NOT_FOUND
    assert prospect(conn, DUPONT)["website_status"] == "NOT_FOUND"
    calls = install_places(monkeypatch)
    _web(web)
    db.execute(conn, "UPDATE local_prospects SET provenance=JSON_REMOVE(COALESCE(provenance, JSON_OBJECT()), '$.places') WHERE siret=%s", (DUPONT,))
    stats = siterecheck.run(conn, search, fetch=web, limit=10)
    p = prospect(conn, DUPONT)
    assert stats["found"] >= 1 and p["website_status"] == "CONFIRMED" and p["website_url"] == f"https://www.{SITE}/"
    assert p["email"] == "contact@pains-d-antan.fr" and "places" in p["provenance"] and SITE not in json.dumps(json.loads(p["provenance"])["places"])
    assert len([c for c in calls if b"DUPONT" in c.content]) == 1


# ── Passage « Google Places sur les fiches déjà trouvées » (bouton de la page Statistiques) ─────────────────────────
def _backfill_setup(conn, monkeypatch, enabled=True, **kw):
    """Fiche DUPONT « sans site », déjà revérifiée AVANT la clé Places (provenance site_recheck, aucune provenance places)."""
    install_api(monkeypatch)
    web, search = web_and_search()
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)                   # Places inactif : NOT_FOUND
    assert prospect(conn, DUPONT)["website_status"] == "NOT_FOUND"
    db.execute(conn, "UPDATE local_prospects SET provenance=JSON_SET(COALESCE(provenance, JSON_OBJECT()), '$.site_recheck', JSON_OBJECT('checked_at', '2026-10-01T00:00:00Z')) "
                     "WHERE siret=%s", (DUPONT,))
    db.execute(conn, "DELETE FROM settings WHERE skey='local_places_backfill'")
    db.execute(conn, "INSERT INTO settings (user_id, skey, value_json) SELECT id, 'local_places_backfill', %s FROM users ORDER BY id LIMIT 1",
               (json.dumps({"enabled": enabled}),))
    if not db.fetch_one(conn, "SELECT 1 AS x FROM settings WHERE skey='local_places_backfill'"):     # aucun utilisateur dans la base de test
        db.execute(conn, "INSERT INTO users (email, password_hash) VALUES ('t@t.fr', 'x')")
        db.execute(conn, "INSERT INTO settings (user_id, skey, value_json) SELECT id, 'local_places_backfill', %s FROM users ORDER BY id LIMIT 1", (json.dumps({"enabled": enabled}),))
    conn.commit()
    calls = install_places(monkeypatch, **kw)
    _web(web)

    def no_engine(q):
        raise AssertionError(f"requête moteur interdite : {q}")
    return calls, web, no_engine


def _enabled(conn) -> bool:
    v = db.fetch_one(conn, "SELECT value_json FROM settings WHERE skey='local_places_backfill'")["value_json"]
    return json.loads(v)["enabled"]


def test_backfill_selection_site_recheck_sans_places_pris_avec_places_ou_contacte_ignore(env, conn, monkeypatch):
    _backfill_setup(conn, monkeypatch)
    pid = prospect(conn, DUPONT)["id"]
    assert pid not in [r["id"] for r in siterecheck.candidates(conn, 50)]                       # déjà site_recheck : hors revérification
    ids = lambda: [r["id"] for r in siterecheck.candidates(conn, 50, siterecheck.NO_PLACES)]    # noqa: E731
    assert pid in ids()
    store_prov = "UPDATE local_prospects SET provenance=JSON_SET(provenance, '$.places', JSON_OBJECT('checked_at', '2026-10-02T00:00:00Z')) WHERE id=%s"
    db.execute(conn, store_prov, (pid,))
    assert pid not in ids()                                                                      # déjà interrogée : ignorée
    db.execute(conn, "UPDATE local_prospects SET provenance=JSON_REMOVE(provenance, '$.places'), contacted_at='2026-10-21 09:00:00' WHERE id=%s", (pid,))
    assert pid not in ids()                                                                      # contactée : ignorée


def test_backfill_site_confirme_sans_requete_moteur_puis_reglage_eteint(env, conn, monkeypatch):
    calls, web, no_engine = _backfill_setup(conn, monkeypatch)
    db.execute(conn, "UPDATE local_prospects SET draft_message='Bonjour, pas de site', draft_kind='sans_site', auto_state='queued' WHERE siret=%s", (DUPONT,))
    conn.commit()
    monkeypatch.setattr(siterecheck, "_no_search", no_engine)                                    # le moindre appel moteur ferait échouer le test
    stats = siterecheck.places_backfill(conn, fetch=web, limit=50)
    p = prospect(conn, DUPONT)
    assert stats["found"] == 1 and p["website_status"] == "CONFIRMED" and p["website_url"] == f"https://www.{SITE}/"
    assert p["email"] == "contact@pains-d-antan.fr" and p["draft_message"] is None and p["auto_state"] is None
    assert json.loads(p["provenance"])["places"]["place_id"] == "ChIJdupont" and SITE not in json.dumps(json.loads(p["provenance"])["places"])
    assert len([c for c in calls if b"DUPONT" in c.content]) == 1
    assert _enabled(conn) is True                                                                # toutes les fiches ne sont pas encore vues : réglage conservé
    siterecheck.places_backfill(conn, fetch=web, limit=50)                                       # plus rien à faire : le réglage s'éteint seul
    assert _enabled(conn) is False


def test_backfill_inactif_sans_reglage_ou_sans_cle_aucun_appel(env, conn, monkeypatch):
    calls, web, _ne = _backfill_setup(conn, monkeypatch, enabled=False)
    assert siterecheck.places_backfill(conn, fetch=web, limit=50)["asked"] == 0 and not calls
    db.execute(conn, "UPDATE settings SET value_json=%s WHERE skey='local_places_backfill'", (json.dumps({"enabled": True}),))
    conn.commit()
    monkeypatch.setattr(places, "API_KEY", "")
    assert siterecheck.places_backfill(conn, fetch=web, limit=50)["asked"] == 0 and not calls
    assert _enabled(conn) is True


def test_backfill_budget_atteint_arret_sans_erreur_reglage_conserve(env, conn, monkeypatch):
    calls, web, _ne = _backfill_setup(conn, monkeypatch, budget=1)
    places.reserve(conn)                                                                         # le mois est épuisé
    stats = siterecheck.places_backfill(conn, fetch=web, limit=50)
    assert stats["asked"] == 0 and not calls and _enabled(conn) is True
