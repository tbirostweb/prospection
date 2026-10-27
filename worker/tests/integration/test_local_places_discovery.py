"""Google Places comme SOURCE de découverte d'une campagne, sur un VRAI MySQL : requêtes (mot-clé × commune) paginées, chaque résultat RATTACHÉ à
son entreprise SIRENE (fiche construite avec les données SIRENE), résultat sans correspondance sûre ignoré, rien de Google en base hors place_id,
plafonds (mensuel, par passage), arrêt net sur 429, même requête pas refaite avant 90 jours. Google, SIRENE et geo.api simulés (aucun appel réel)."""
from __future__ import annotations

import json

import httpx
import pytest
from local_fixtures import FakeSearch, FakeWeb, html

from worker import db
from worker.local import http, places, placesdiscovery, runner

from .test_local_pipeline import etab, unit

MARTIN, DUPONT = "11122233300011", "22233344400015"
SITE = "dupont-plomberie.fr"
GOOGLE_ONLY = ("Google-Only", "0311111111", "03 11 11 11 11", "Inconnu", "48.3001", "utm=gmb", "4.0712")   # contenu Google : jamais en base


def g(pid, name, address, lat, lon, site=None, status="OPERATIONAL"):
    p = {"id": pid, "displayName": {"text": name, "languageCode": "fr"}, "formattedAddress": address, "businessStatus": status,
         "location": {"latitude": lat, "longitude": lon}, "nationalPhoneNumber": "03 11 11 11 11"}
    if site:
        p["websiteUri"] = site
    return p


PAGE1 = [g("ChIJmartin", "Plomberie Martin", "3 Rue Émile Zola, 10000 Troyes, France", 48.2905, 4.0712),
         g("ChIJdupont", "Dupont Plomberie", "7 Rue Google-Only, 10000 Troyes, France", 48.3001, 4.0701, site=f"https://www.{SITE}/?utm=gmb"),
         g("ChIJinconnu", "Plombier Inconnu Express", "1 Rue Google-Only, 10000 Troyes, France", 48.2950, 4.0750, site="https://inconnu-express.fr/")]
PAGE2 = [g("ChIJdurand", "Agence Web Durand", "2 Rue Google-Only, 10000 Troyes, France", 48.2960, 4.0760),
         g("ChIJleroux", "Plomberie Leroux", "9 Rue Google-Only, 10000 Troyes, France", 48.2970, 4.0770)]


def sirene_handler(req: httpx.Request):
    if req.url.host == "recherche-entreprises.api.gouv.fr" and req.url.path == "/near_point":
        page = int(req.url.params["page"])
        units = [unit("111222333", "PLOMBERIE MARTIN", [etab(MARTIN, naf="43.22A", lat="48.2904", lon="4.0711", street="3 RUE EMILE ZOLA")])]
        return httpx.Response(200, json={"results": units if page == 1 else [], "total_pages": 1})
    if req.url.host == "recherche-entreprises.api.gouv.fr" and req.url.path == "/search":
        assert req.url.params["code_postal"] == "10000" and req.url.params["etat_administratif"] == "A"
        q = req.url.params["q"]
        found = {
            "Dupont Plomberie": [unit("222333444", "DUPONT PLOMBERIE", [etab(DUPONT, naf="43.22A", lat="48.3", lon="4.07", street="7 RUE DE LA PAIX")])],
            "Agence Web Durand": [unit("444555666", "AGENCE WEB DURAND", [etab("44455566600011", naf="62.01Z", lat="48.296", lon="4.076")])],
            "Plomberie Leroux": [unit("333444555", "PLOMBERIE LEROUX", [etab("33344455500011", naf="43.22A", lat="48.297", lon="4.077")]),
                                 unit("333444666", "PLOMBERIE LEROUX", [etab("33344466600011", naf="43.22A", lat="48.2971", lon="4.0771")])],
        }.get(q, [])
        return httpx.Response(200, json={"results": found, "total_pages": 1})
    if req.url.host == "geo.api.gouv.fr" and req.url.path == "/departements/10/communes":
        return httpx.Response(200, json=[
            {"nom": "Troyes", "code": "10387", "centre": {"type": "Point", "coordinates": [4.0761, 48.2924]}, "population": 60000},
            {"nom": "Sainte-Savine", "code": "10362", "centre": {"type": "Point", "coordinates": [4.05, 48.296]}, "population": 10000},
            {"nom": "Petit-Village", "code": "10999", "centre": {"type": "Point", "coordinates": [4.1, 48.30]}, "population": 100},
            {"nom": "Loin", "code": "10500", "centre": {"type": "Point", "coordinates": [4.4, 47.9]}, "population": 5000}])
    raise AssertionError(f"appel inattendu : {req.url}")


@pytest.fixture(autouse=True)
def _isole(env, conn, monkeypatch):
    monkeypatch.setattr(places, "_blocked", False)
    places._session.clear()
    db.execute(conn, "DELETE FROM local_places_usage")
    monkeypatch.setattr(http, "_transport", httpx.MockTransport(sirene_handler))
    monkeypatch.setattr(placesdiscovery, "PER_RUN", 20)
    monkeypatch.setattr(placesdiscovery, "MIN_POPULATION", 500)
    yield
    places._transport = None
    places._session.clear()


def install_google(monkeypatch, budget=900, status=200, key="cle-test"):
    calls: list[dict] = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        calls.append({"body": body, "headers": dict(req.headers)})
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "quota"}})
        if "maxResultCount" in body:                                   # recherche de site d'un prospect existant (places.candidate_url)
            return httpx.Response(200, json={"places": []})
        if "Troyes" not in body["textQuery"]:
            return httpx.Response(200, json={})                        # autre commune : aucune fiche
        if body.get("pageToken") == "PAGE2":
            return httpx.Response(200, json={"places": PAGE2})
        assert "pageToken" not in body
        return httpx.Response(200, json={"places": PAGE1, "nextPageToken": "PAGE2"})
    monkeypatch.setattr(places, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(places, "API_KEY", key)
    monkeypatch.setattr(places, "MONTHLY_BUDGET", budget)
    return calls


def web():
    w = FakeWeb()
    legal = html(title="Mentions légales", h1="Mentions légales",
                 body="<p>DUPONT PLOMBERIE - SIRET 222 333 444 00015 - 7 rue de la Paix 10000 Troyes - 03 25 70 80 90 - contact@dupont-plomberie.fr</p>")
    home = html(title="Dupont Plomberie - Troyes", h1="Dupont Plomberie", canonical=False, body='<a href="/mentions-legales">Mentions légales</a>')
    w.add_site(SITE, home, legal=legal)
    w.add_site(f"www.{SITE}", home, legal=legal)
    return w


def campaign(conn, name="Plombiers Troyes"):
    row = dict(name=name, city="Troyes", postal_code="10000", department="10", commune_code="10387", latitude=48.2924, longitude=4.0761,
               radius_km=10, activities=json.dumps(["plombiers"]), max_companies=100, status="queued")
    cid = db.execute(conn, f"INSERT INTO local_campaigns ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})", tuple(row.values()))
    conn.commit()
    return db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (cid,))


def run(conn, camp):
    return runner.run_campaign(conn, camp, search=FakeSearch(), fetch=web())


def prospect(conn, siret):
    return db.fetch_one(conn, "SELECT * FROM local_prospects WHERE siret=%s", (siret,))


def discovery_calls(calls):
    return [c for c in calls if "pageSize" in c["body"]]


def test_campagne_pagination_rattachement_sirene_et_site_verifie(env, conn, monkeypatch):
    calls = install_google(monkeypatch)
    camp = campaign(conn)
    stats = run(conn, camp)

    d = discovery_calls(calls)
    troyes = [c for c in d if "Troyes" in c["body"]["textQuery"]]
    assert [c["body"].get("pageToken") for c in troyes] == [None, "PAGE2"]                      # pagination : 2 pages pour « plombier Troyes »
    assert {c["body"]["textQuery"] for c in d} == {"plombier Troyes", "plombier Sainte-Savine"}  # Petit-Village (< 500 hab.) et Loin (hors rayon) : non
    b = troyes[0]["body"]
    assert b["pageSize"] == 20 and b["regionCode"] == "FR" and set(b["locationRestriction"]["rectangle"]) == {"low", "high"}
    mask = troyes[0]["headers"]["x-goog-fieldmask"]
    assert "nextPageToken" in mask and "places.websiteUri" in mask and "Phone" not in mask
    assert len(calls) == len(d) == 3 and places.month_used(conn) == 3                            # aucune 2e requête pour les fiches rattachées

    dupont = prospect(conn, DUPONT)                                                              # absente de SIRENE « près de » : trouvée par Google
    assert dupont is not None and dupont["company_name"] == "DUPONT PLOMBERIE" and dupont["address"].startswith("7 RUE DE LA PAIX")   # données SIRENE
    assert dupont["website_status"] == "CONFIRMED" and dupont["website_url"] == f"https://www.{SITE}/"                              # vérifié sur le site
    assert dupont["email"] == "contact@dupont-plomberie.fr" and dupont["phone"] == "0325708090"                                     # lus sur le site
    prov = json.loads(dupont["provenance"])["places"]
    assert set(prov) == {"checked_at", "place_id", "had_website", "site_checked_at", "site_checked"} and prov["place_id"] == "ChIJdupont"
    assert prov["had_website"] is True and prov["site_checked"] == "confirmed"
    src = {r["source"]: r["source_ref"] for r in db.fetch_all(conn, "SELECT source, source_ref FROM local_prospect_sources WHERE prospect_id=%s", (dupont["id"],))}
    assert src["places"] == "ChIJdupont" and "sirene" not in src

    martin = prospect(conn, MARTIN)                                                              # déjà trouvée par SIRENE : juste la provenance
    src = {r["source"]: r["source_ref"] for r in db.fetch_all(conn, "SELECT source, source_ref FROM local_prospect_sources WHERE prospect_id=%s", (martin["id"],))}
    assert src["places"] == "ChIJmartin" and "sirene" in src and json.loads(martin["provenance"])["places"]["had_website"] is False

    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects")["n"] == 2            # Inconnu (pas dans SIRENE), Leroux (2 entreprises
    assert prospect(conn, "44455566600011") is None and prospect(conn, "33344455500011") is None  # possibles), agence web (NAF exclu) : ignorés
    assert (stats["places_matched"], stats["places_created"], stats["places_unmatched"], stats["places_banned"], stats["places_requests"]) == (2, 1, 2, 1, 3)
    assert stats["places_done"] is True and db.fetch_one(conn, "SELECT status FROM local_campaigns WHERE id=%s", (camp["id"],))["status"] == "done"
    q = db.fetch_one(conn, "SELECT * FROM local_places_queries WHERE commune_code='10387'")
    assert (q["keyword"], q["pages"], q["results"], q["matched"], q["created"], q["unmatched"]) == ("plombier", 2, 5, 2, 1, 2)
    linked = {r["prospect_id"] for r in db.fetch_all(conn, "SELECT prospect_id FROM local_places_query_prospects WHERE query_id=%s", (q["id"],))}
    assert linked == {martin["id"], dupont["id"]}

    dump = json.dumps([db.fetch_all(conn, f"SELECT * FROM {t}") for t in ("local_prospects", "local_prospect_sources", "local_places_queries",
                                                                           "local_places_query_prospects", "local_campaigns", "local_events")], default=str)
    for marker in GOOGLE_ONLY:                                                                   # rien de Google en base hors place_id
        assert marker not in dump, marker


def test_requete_pas_refaite_avant_90_jours_et_reprise_par_une_autre_campagne(env, conn, monkeypatch):
    calls = install_google(monkeypatch)
    run(conn, campaign(conn))
    n = len(calls)
    camp2 = campaign(conn, "Plombiers Troyes bis")
    stats: dict = {}
    pending = placesdiscovery.run(conn, camp2, runner.Ctx(conn, camp2), stats, fetch=web())
    assert len(calls) == n and pending is False and stats["places_done"] is True                # mémorisée : aucun appel
    ids = {r["prospect_id"] for r in db.fetch_all(conn, "SELECT prospect_id FROM local_prospect_campaigns WHERE campaign_id=%s", (camp2["id"],))}
    assert ids == {prospect(conn, MARTIN)["id"], prospect(conn, DUPONT)["id"]}                   # ses fiches rejoignent la 2e campagne
    db.execute(conn, "UPDATE local_places_queries SET queried_at = UTC_TIMESTAMP() - INTERVAL 91 DAY")
    placesdiscovery.run(conn, camp2, runner.Ctx(conn, camp2), {}, fetch=web())
    assert len(calls) == n + 3                                                                   # plus de 90 jours : refaite (2 pages + 1)
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects")["n"] == 2            # aucun doublon


def test_budget_mensuel_respecte_requete_non_memorisee_campagne_en_attente(env, conn, monkeypatch):
    calls = install_google(monkeypatch, budget=1)
    camp = campaign(conn)
    stats = run(conn, camp)
    assert len(calls) == 1 and places.month_used(conn) == 1                                      # page 2 : budget atteint, aucun appel
    assert "budget" in stats["places_waiting"] and not stats.get("places_done")
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_places_queries")["n"] == 0        # incomplète : refaite plus tard
    assert db.fetch_one(conn, "SELECT status FROM local_campaigns WHERE id=%s", (camp["id"],))["status"] == "running"
    assert prospect(conn, DUPONT) is not None                                                    # la page 1 a quand même servi


def test_arret_net_sur_429(env, conn, monkeypatch):
    calls = install_google(monkeypatch, status=429)
    camp = campaign(conn)
    stats = run(conn, camp)
    assert len(calls) == 1 and places.month_used(conn) == 0 and places._blocked is True          # un essai, requête rendue, plus aucun appel
    assert stats["places_retry_at"] > 0 and not stats.get("places_done")
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_places_queries")["n"] == 0
    assert prospect(conn, MARTIN)["website_status"] == "NOT_FOUND"                              # la campagne continue sans Google
    assert db.fetch_one(conn, "SELECT status FROM local_campaigns WHERE id=%s", (camp["id"],))["status"] == "running"
    monkeypatch.setattr(places, "_blocked", False)                                               # passage suivant (nouveau processus) : attente d'une heure
    camp = db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp["id"],))
    run(conn, camp)
    assert len(discovery_calls(calls)) == 1


def test_plafond_par_passage(env, conn, monkeypatch):
    calls = install_google(monkeypatch)
    monkeypatch.setattr(placesdiscovery, "PER_RUN", 1)
    camp = campaign(conn)
    stats = run(conn, camp)
    assert len(discovery_calls(calls)) == 2                                                      # la requête commencée va jusqu'à sa dernière page
    assert stats["places_units_left"] == 1 and not stats.get("places_done")
    assert db.fetch_one(conn, "SELECT status FROM local_campaigns WHERE id=%s", (camp["id"],))["status"] == "running"
    stats = run(conn, db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp["id"],)))
    assert len(discovery_calls(calls)) == 3 and stats["places_done"] is True                     # passage suivant : la commune restante


def test_sans_cle_aucun_appel(env, conn, monkeypatch):
    calls = install_google(monkeypatch, key="")
    stats = run(conn, campaign(conn))
    assert calls == [] and "places_requests" not in stats and prospect(conn, DUPONT) is None
