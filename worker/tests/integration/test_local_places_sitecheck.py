"""Sites de fiche Google PERDUS, sur un VRAI MySQL : fiche interrogée (place_id, had_website) mais jamais vérifiée — l'URL n'était qu'en mémoire.
Place Details (champ websiteUri seul) la redemande, le site est vérifié sur lui-même (barème du site déclaré), e-mail lu sur le site ; verdict
posé (`places.site_checked`) : plus aucune requête ; seul le place_id de Google en base. Google simulé (aucun appel réel)."""
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


@pytest.fixture(autouse=True)
def _places_isole(env, conn, monkeypatch):
    monkeypatch.setattr(places, "_blocked", False)
    places._session.clear()
    db.execute(conn, "DELETE FROM local_places_usage")
    yield
    places._transport = None
    places._session.clear()


def _site(web):
    """Site de la boulangerie SANS SIRET ni adresse : nom + ville seulement (rejeté par le barème des résultats de recherche)."""
    home = html(title="Boulangerie Dupont - Troyes", h1="Boulangerie Dupont", canonical=False, body='<a href="/contact">Nous contacter</a>')
    for host in (SITE, f"www.{SITE}"):
        web.add_site(host, home)
        web.pages[f"https://{host}/contact"] = (200, html(title="Contact", h1="Contact", canonical=False,
                                                           body="<p>Écrivez-nous : bonjour [at] pains-d-antan [point] fr</p>"))


def _lost(conn, monkeypatch, website=f"https://www.{SITE}/?utm_source=gmb", status=200, site_checked=None):
    """DUPONT « sans site » (Places inactif), puis provenance d'une fiche Google interrogée avec un site mais jamais vérifiée (URL perdue)."""
    install_api(monkeypatch)
    web, search = web_and_search()
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    assert prospect(conn, DUPONT)["website_status"] == "NOT_FOUND"
    flag = {"checked_at": "2026-10-20T08:00:00Z", "place_id": "ChIJdupont", "had_website": True}
    if site_checked:
        flag.update(site_checked_at="2026-10-21T08:00:00Z", site_checked=site_checked)
    db.execute(conn, """UPDATE local_prospects SET provenance=JSON_SET(provenance, '$.places', CAST(%s AS JSON)),
                        draft_message='Bonjour, je n''ai pas trouvé de site', draft_kind='sans_site', auto_state='queued' WHERE siret=%s""",
               (json.dumps(flag), DUPONT))
    conn.commit()
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request):
        calls.append(req)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "refusé"}})
        assert req.method == "GET" and req.url.path == "/v1/places/ChIJdupont"           # Place Details, jamais une nouvelle Text Search
        return httpx.Response(200, json={"websiteUri": website} if website else {})
    monkeypatch.setattr(places, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(places, "API_KEY", "cle-test")
    monkeypatch.setattr(places, "MONTHLY_BUDGET", 900)
    _site(web)

    def no_engine(q):
        raise AssertionError(f"requête moteur interdite : {q}")
    monkeypatch.setattr(siterecheck, "_no_search", no_engine)
    return calls, web


def _places(conn) -> dict:
    return json.loads(prospect(conn, DUPONT)["provenance"])["places"]


def test_place_details_recupere_l_url_perdue_site_probable_et_email_ajoute(env, conn, monkeypatch):
    calls, web = _lost(conn, monkeypatch)
    stats = siterecheck.places_site_check(conn, fetch=web, limit=10)
    p = prospect(conn, DUPONT)
    assert stats == {"asked": 1, "found": 1, "rejected": 0}
    assert p["website_status"] == "PROBABLE" and p["website_url"] == f"https://www.{SITE}/"
    assert p["email"] == "bonjour@pains-d-antan.fr"                                          # adresse masquée, lue sur la page contact
    assert p["draft_message"] is None and p["auto_state"] is None and p["category"] is not None   # brouillon « sans site » effacé, fiche rescorée
    assert len(calls) == 1 and calls[0].headers["X-Goog-FieldMask"] == "websiteUri" and calls[0].headers["X-Goog-Api-Key"] == "cle-test"
    assert places.month_used(conn) == 1
    pl = _places(conn)
    assert pl["site_checked"] == "probable" and pl["site_checked_at"] and pl["place_id"] == "ChIJdupont"
    assert set(pl) == {"checked_at", "place_id", "had_website", "site_checked_at", "site_checked"}   # rien d'autre de Google que le place_id
    assert SITE not in json.dumps(pl) and "utm_source" not in (p["provenance"] or "")


def test_pas_de_nouvelle_requete_une_fois_le_site_juge(env, conn, monkeypatch):
    calls, web = _lost(conn, monkeypatch)
    siterecheck.places_site_check(conn, fetch=web, limit=10)
    n = len(calls)
    places._session.clear()                                                                  # autre processus : plus rien en mémoire
    assert siterecheck.places_site_check(conn, fetch=web, limit=10)["asked"] == 0
    row = prospect(conn, DUPONT)
    assert places.candidate_url(conn, row, runner._company(conn, row)) is None and len(calls) == n


def test_site_deja_juge_jamais_redemande(env, conn, monkeypatch):
    calls, web = _lost(conn, monkeypatch, site_checked="rejected:no_match")
    assert siterecheck.places_site_check(conn, fetch=web, limit=10)["asked"] == 0 and calls == []


@pytest.mark.parametrize("website, verdict", [
    ("https://www.pagesjaunes.fr/pros/12345678", "rejected:directory"),
    ("https://www.facebook.com/boulangeriedupont", "rejected:social"),
    (None, "no_url"),
])
def test_annuaire_reseau_social_ou_sans_site_rejetes_sans_lecture(env, conn, monkeypatch, website, verdict):
    calls, web = _lost(conn, monkeypatch, website=website)
    web.calls.clear()
    socials_before = prospect(conn, DUPONT)["social_links"]
    stats = siterecheck.places_site_check(conn, fetch=web, limit=10)
    p = prospect(conn, DUPONT)
    assert stats == {"asked": 1, "found": 0, "rejected": 1} and len(calls) == 1 and web.calls == []
    assert p["website_status"] == "NOT_FOUND" and _places(conn)["site_checked"] == verdict
    assert p["social_links"] == socials_before                                               # profil venu de Google : jamais conservé (conditions Places)


def test_site_d_une_autre_entreprise_rejete_avec_sa_raison(env, conn, monkeypatch):
    calls, web = _lost(conn, monkeypatch, website="https://www.autre-boulangerie.fr/")
    web.add_site("www.autre-boulangerie.fr", html(title="Boulangerie Lefèvre - Reims", h1="Boulangerie Lefèvre", canonical=False,
                                                  body="<p>LEFEVRE SARL - SIRET 999 999 999 00011 - 51100 Reims</p>"))
    stats = siterecheck.places_site_check(conn, fetch=web, limit=10)
    p = prospect(conn, DUPONT)
    assert stats["rejected"] == 1 and p["website_status"] == "NOT_FOUND" and p["draft_message"] is not None
    assert _places(conn)["site_checked"].startswith("rejected:")


def test_quota_google_rien_n_est_ecrit_et_arret(env, conn, monkeypatch):
    calls, web = _lost(conn, monkeypatch, status=429)
    stats = siterecheck.places_site_check(conn, fetch=web, limit=10)
    assert stats["asked"] == 0 and len(calls) == 1 and places._blocked and places.month_used(conn) == 0   # requête rendue
    assert "site_checked_at" not in _places(conn)                                            # reprise au prochain passage


def test_runner_redemande_l_url_perdue_dans_un_autre_processus(env, conn, monkeypatch):
    """Recherche de site (revérification) d'une fiche à l'URL perdue : Place Details au lieu de rien, puis verdict posé."""
    calls, web = _lost(conn, monkeypatch)
    db.execute(conn, "UPDATE local_prospects SET provenance=JSON_REMOVE(provenance, '$.site_recheck') WHERE siret=%s", (DUPONT,))
    conn.commit()
    _st, saved = siterecheck.recheck(conn, prospect(conn, DUPONT), lambda q: [], web)
    assert saved and prospect(conn, DUPONT)["website_status"] == "PROBABLE" and len(calls) == 1
    assert _places(conn)["site_checked"] == "probable"
