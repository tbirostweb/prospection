"""Résilience de la prospection locale : isolation par prospect, reprise, backoff, couverture, retours utilisateur, OSM, reset."""
from __future__ import annotations

import json

import httpx

from worker import db, reset
from worker.local import errors, osm, runner, sitefinder, store
from worker.local.sitefinder import Hit

from local_fixtures import LEGAL_MARTIN, FakeSearch, FakeWeb, html
from .test_local_pipeline import add_campaign, bodacc_creations, etab, install_api, prospect, sirene_units, unit, web_and_search   # noqa: F401


def state(conn, siret, cols="*"):
    return db.fetch_one(conn, f"SELECT {cols} FROM local_prospects WHERE siret=%s", (siret,))


def rerun(conn, camp, **kw):
    db.execute(conn, "UPDATE local_campaigns SET status='queued' WHERE id=%s", (camp["id"],))
    conn.commit()
    return runner.run_campaign(conn, db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp["id"],)), **kw)


class Boom(FakeWeb):
    """Le réseau « plante » (exception non prévue) pour un domaine donné."""

    def __init__(self, bad: str, *a, **k):
        super().__init__(*a, **k)
        self.bad = bad

    def __call__(self, url):
        if self.bad in url:
            raise RuntimeError("analyse HTML cassée")
        return super().__call__(url)


def test_un_prospect_qui_plante_n_arrete_pas_la_campagne_et_est_repris_avec_backoff(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    boom = Boom("salon-jolie.fr", web.pages, web.redirects)
    camp = add_campaign(conn)
    stats = runner.run_campaign(conn, camp, search=search, fetch=boom)
    assert db.fetch_one(conn, "SELECT status FROM local_campaigns WHERE id=%s", (camp["id"],))["status"] == "running"      # pas « error » : les autres prospects sont traités
    bad = state(conn, "77777777700011")
    assert bad["pipeline_stage"] == "ERROR" and bad["attempts"] == 1 and bad["error_category"] == errors.UNEXPECTED and "analyse HTML cassée" in bad["last_error"]
    assert bad["next_retry_at"] is not None and bad["website_status"] is None                # rien n'a été conclu à tort
    ok = state(conn, "11111111100011")
    assert ok["website_status"] == "CONFIRMED" and ok["pipeline_stage"] == "DONE"            # les autres ont abouti
    assert stats["errors"] == 1 and db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_events WHERE category=%s AND prospect_id=%s", (errors.UNEXPECTED, bad["id"]))["n"] == 1

    # avant l'échéance du backoff : pas de nouvelle tentative
    search.queries.clear()
    rerun(conn, camp, search=search, fetch=web)
    assert state(conn, "77777777700011")["attempts"] == 1 and not any("SALON JOLIE" in q for q in search.queries)
    # échéance atteinte : repris, et le succès efface l'erreur
    db.execute(conn, "UPDATE local_prospects SET next_retry_at = UTC_TIMESTAMP() - INTERVAL 1 MINUTE WHERE siret='77777777700011'")
    conn.commit()
    rerun(conn, camp, search=search, fetch=web)
    fixed = state(conn, "77777777700011")
    assert fixed["website_status"] == "CONFIRMED" and fixed["pipeline_stage"] == "DONE" and fixed["attempts"] == 0 and fixed["error_category"] is None


def test_abandon_apres_le_nombre_maximal_de_tentatives(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    boom = Boom("salon-jolie.fr", web.pages, web.redirects)
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=boom)
    for _ in range(4):
        db.execute(conn, "UPDATE local_prospects SET next_retry_at = UTC_TIMESTAMP() - INTERVAL 1 MINUTE WHERE siret='77777777700011' AND next_retry_at IS NOT NULL")
        conn.commit()
        rerun(conn, camp, search=search, fetch=boom)
    p = state(conn, "77777777700011")
    assert p["pipeline_stage"] == "ERROR" and p["next_retry_at"] is None and p["attempts"] == errors.POLICY[errors.UNEXPECTED][1]     # abandonné, visible
    calls = len(search.queries)
    rerun(conn, camp, search=search, fetch=boom)
    assert len(search.queries) == calls                                                     # plus jamais retenté automatiquement
    assert db.fetch_one(conn, "SELECT status FROM local_campaigns WHERE id=%s", (camp["id"],))["status"] == "done"     # une campagne ne reste pas bloquée pour un prospect abandonné


def test_reprise_apres_crash_pendant_l_audit_sans_rechercher_le_site_deux_fois(env, conn, monkeypatch):
    monkeypatch.setattr(runner, "AUDIT", True)                                              # audit activé (LOCAL_AUDIT=1)
    install_api(monkeypatch)
    web, search = web_and_search()
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=web)
    pid = state(conn, "11111111100011", "id")["id"]
    # simule un crash après la validation du site : audit et contacts jamais écrits
    db.execute(conn, "UPDATE local_prospects SET audited_at=NULL, contact_discovered_at=NULL, phone=NULL, email=NULL, provenance=JSON_REMOVE(provenance, '$.contacts'), "
                     "pipeline_stage='AUDIT' WHERE id=%s", (pid,))
    conn.commit()
    search.queries.clear()
    rerun(conn, camp, search=search, fetch=web)
    p = db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (pid,))
    assert search.queries == []                                                             # le site n'est PAS recherché de nouveau
    assert p["audited_at"] and p["email"] == "contact@bistrot-martin.fr" and p["pipeline_stage"] == "DONE" and p["website_status"] == "CONFIRMED"


def test_site_illisible_a_la_reprise_est_un_echec_a_reessayer_pas_un_site_sans_contact(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=web)
    pid = state(conn, "11111111100011", "id")["id"]
    db.execute(conn, "UPDATE local_prospects SET audited_at=NULL, provenance=JSON_REMOVE(provenance, '$.contacts') WHERE id=%s", (pid,))
    conn.commit()
    rerun(conn, camp, search=search, fetch=FakeWeb())                                       # le site ne répond plus à cet instant
    p = db.fetch_one(conn, "SELECT pipeline_stage, error_category, audited_at FROM local_prospects WHERE id=%s", (pid,))
    assert p["pipeline_stage"] == "ERROR" and p["error_category"] == errors.SITE_TIMEOUT and p["audited_at"] is None


def test_recherche_indisponible_met_en_attente_sans_toucher_aux_prospects(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, _ = web_and_search()

    def down(query):
        raise errors.SearchUnavailable("tous les moteurs sont en cooldown", errors.SEARCH_CAPTCHA)
    camp = add_campaign(conn)
    stats = runner.run_campaign(conn, camp, search=down, fetch=web)
    assert "cooldown" in stats["waiting"] and db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects WHERE website_status IS NOT NULL")["n"] == 0
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects WHERE pipeline_stage='ERROR'")["n"] == 0        # pas d'erreur imputée au prospect
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_events WHERE category=%s", (errors.SEARCH_CAPTCHA,))["n"] >= 1
    assert db.fetch_one(conn, "SELECT status FROM local_campaigns WHERE id=%s", (camp["id"],))["status"] == "running"     # jamais « terminée »
    web2, search = web_and_search()
    stats = rerun(conn, camp, search=search, fetch=web2)                                    # les moteurs sont revenus : la campagne reprend
    assert "waiting" not in stats and db.fetch_one(conn, "SELECT status FROM local_campaigns WHERE id=%s", (camp["id"],))["status"] == "done"


def test_mysql_momentanement_indisponible_ne_perd_rien(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    camp = add_campaign(conn)
    real = store.save_resolution
    boom = {"n": 0}

    def flaky(c, pid, res, socials=None):
        if boom["n"] == 0:
            boom["n"] += 1
            import pymysql
            raise pymysql.err.OperationalError(2006, "MySQL server has gone away")
        return real(c, pid, res, socials)
    monkeypatch.setattr(store, "save_resolution", flaky)
    runner.run_campaign(conn, camp, search=search, fetch=web)
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects WHERE error_category=%s", (errors.DB_ERROR,))["n"] == 1
    db.execute(conn, "UPDATE local_prospects SET next_retry_at = UTC_TIMESTAMP() - INTERVAL 1 MINUTE WHERE error_category IS NOT NULL")
    conn.commit()
    rerun(conn, camp, search=search, fetch=web)
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects WHERE pipeline_stage='ERROR'")["n"] == 0


def test_couverture_de_la_decouverte_partielle_est_enregistree(env, conn, monkeypatch):
    def handler(req):
        if req.url.host == "recherche-entreprises.api.gouv.fr":
            page = int(req.url.params["page"])
            units = [unit(f"{page}00000{i:03d}", f"RESTO {page}-{i}", [etab(f"{page}00000{i:03d}00011", street=f"{i} RUE ZOLA")]) for i in range(25)]
            return httpx.Response(200, json={"results": units, "total_pages": 40, "total_results": 1000})
        return httpx.Response(200, json={"results": []})
    from worker.local import http
    monkeypatch.setattr(http, "_transport", httpx.MockTransport(handler))
    row = db.execute(conn, """INSERT INTO local_campaigns (name, city, postal_code, department, latitude, longitude, radius_km, activities, max_companies, status)
                              VALUES ('Grande zone','Troyes','10000','10',48.29,4.07,60,'["restaurants"]',30,'queued')""")
    conn.commit()
    runner.run_campaign(conn, db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (row,)), search=FakeSearch(), fetch=FakeWeb())
    cov = json.loads(db.fetch_one(conn, "SELECT coverage FROM local_campaigns WHERE id=%s", (row,))["coverage"])
    assert cov["partial"] is True and cov["limit_hit"] is True and cov["stopped"] in ("company_limit", "page_limit")
    assert cov["units_available"] == 1000 and cov["pages"] >= 1 and cov["imported"] >= 30 and cov["radius_capped"] is True      # rayon 60 km > plafond API de 50 km


def test_couverture_complete_quand_la_zone_est_epuisee(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=web)
    cov = json.loads(db.fetch_one(conn, "SELECT coverage FROM local_campaigns WHERE id=%s", (camp["id"],))["coverage"])
    assert cov["partial"] is False and cov["stopped"] == "exhausted" and cov["imported"] >= 7


def test_commune_ambigue_est_refusee_avec_alternatives(env, conn, monkeypatch):
    def handler(req):
        return httpx.Response(200, json=[
            {"nom": "Saint-Denis", "code": "97411", "codesPostaux": ["97400"], "population": 155634, "centre": {"coordinates": [55.45, -20.88]}, "departement": {"code": "974"}},
            {"nom": "Saint-Denis", "code": "93066", "codesPostaux": ["93200"], "population": 149077, "centre": {"coordinates": [2.36, 48.93]}, "departement": {"code": "93"}}])
    from worker.local import http
    monkeypatch.setattr(http, "_transport", httpx.MockTransport(handler))
    cid = db.execute(conn, "INSERT INTO local_campaigns (name, city, radius_km, activities, status) VALUES ('SD','Saint-Denis',5,'[\"restaurants\"]','queued')")
    conn.commit()
    runner.run_campaign(conn, db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (cid,)), search=FakeSearch(), fetch=FakeWeb())
    c = db.fetch_one(conn, "SELECT status, last_error, latitude FROM local_campaigns WHERE id=%s", (cid,))
    assert c["status"] == "error" and "ambiguë" in c["last_error"] and "974" in c["last_error"] and "93" in c["last_error"] and c["latitude"] is None
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects")["n"] == 0        # jamais de choix silencieux « la plus peuplée »


# ── retours utilisateur : « Mauvais site » et « J'ai trouvé le site » ───────────────────────────────────
def test_mauvais_site_dissocie_ne_revient_pas_et_relance_la_recherche(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=web)
    p = state(conn, "11111111100011")
    assert p["website_status"] == "CONFIRMED" and p["canonical_domain"] == "bistrot-martin.fr"
    # ce que fait le web au clic « Mauvais site »
    db.execute(conn, "INSERT INTO local_bad_sites (siret, fingerprint, domain, reason) VALUES (%s,%s,%s,'mauvais site')", (p["siret"], p["fingerprint"], p["canonical_domain"]))
    db.execute(conn, """UPDATE local_prospects SET website_status=NULL, website_url=NULL, website_confidence=NULL, canonical_domain=NULL, audited_at=NULL, provenance=NULL,
                        pipeline_stage='DISCOVERED' WHERE id=%s""", (p["id"],))
    conn.commit()
    search.queries.clear()
    web.calls.clear()
    rerun(conn, camp, search=search, fetch=web)
    after = state(conn, "11111111100011")
    assert after["website_status"] != "CONFIRMED" and after["website_url"] is None            # le domaine rejeté n'est plus jamais proposé
    assert search.queries                                                                     # la recherche a bien été relancée
    assert not any("bistrot-martin.fr" in c for c in web.calls)                              # pas même téléchargé de nouveau


def test_j_ai_trouve_le_site_est_valide_analyse_et_sans_apprentissage_global(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    web.add_site("chez-dupont-pains.fr", html(title="Chez Dupont, pains d'exception", h1="Chez Dupont", body='<a href="/mentions-legales">ML</a><p>5 rue Voltaire 10000 Troyes</p>'),
                 legal=html(title="Mentions", body="<p>BOULANGERIE DUPONT - SIRET 222 222 222 00011 - 5 rue Voltaire 10000 Troyes - 03 25 00 11 22</p>"))
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=web)
    p = state(conn, "22222222200011")
    assert p["website_status"] == "NOT_FOUND"
    fb = db.execute(conn, "INSERT INTO local_site_feedback (prospect_id, siret, kind, url) VALUES (%s,%s,'found_site','https://chez-dupont-pains.fr/')", (p["id"], p["siret"]))
    db.execute(conn, "UPDATE local_prospects SET website_status=NULL, pipeline_stage='DISCOVERED' WHERE id=%s", (p["id"],))
    conn.commit()
    rerun(conn, camp, search=search, fetch=web)
    after = state(conn, "22222222200011")
    assert after["website_status"] == "CONFIRMED" and after["canonical_domain"] == "chez-dupont-pains.fr"           # validé par SIRET + adresse, pas sur parole
    row = db.fetch_one(conn, "SELECT analysis, processed_at FROM local_site_feedback WHERE id=%s", (fb,))
    assert row["processed_at"] is not None
    analysis = json.loads(row["analysis"])
    assert "aucune des recherches" in analysis["why"] and analysis["status_now"] == "CONFIRMED"      # pourquoi la résolution l'avait manqué
    other = state(conn, "11111111100011")
    assert other["website_url"] == "https://www.bistrot-martin.fr/"                                   # aucun effet sur les autres prospects


def test_j_ai_trouve_le_site_faux_n_est_pas_accepte_sur_parole(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    web.add_site("tout-autre-chose.fr", html(title="Garage Durand", h1="Garage Durand", body="<p>Lyon 69000</p>"))
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=web)
    p = state(conn, "22222222200011")
    db.execute(conn, "INSERT INTO local_site_feedback (prospect_id, siret, kind, url) VALUES (%s,%s,'found_site','https://tout-autre-chose.fr/')", (p["id"], p["siret"]))
    db.execute(conn, "UPDATE local_prospects SET website_status=NULL WHERE id=%s", (p["id"],))
    conn.commit()
    rerun(conn, camp, search=search, fetch=web)
    assert state(conn, "22222222200011")["website_status"] in ("NOT_FOUND", "UNCERTAIN")


# ── OSM ───────────────────────────────────────────────────────────────────────────────────────────────
def overpass(elements, calls=None):
    def handler(req: httpx.Request):
        if calls is not None:
            calls.append(str(req.url))
        return httpx.Response(200, json={"elements": elements})
    return httpx.MockTransport(handler)


def test_moteurs_satures_les_fiches_a_rechercher_attendent_le_passage_suivant(env, conn, monkeypatch):
    """Après une SearchUnavailable, les fiches qui ont besoin d'une recherche ne sont plus traitées (ni DNS, ni domaines devinés lus) ; elles
    restent intactes et sont reprises au passage suivant, sans perte."""
    install_api(monkeypatch)
    web, search = web_and_search()
    legal = html(title="Mentions légales", body="<p>LE BISTROT MARTIN - SIRET 111 111 111 00011 - 12 rue Emile Zola 10000 Troyes</p>")
    web.add_site("bistrot-martin.fr", html(title="Le Bistrot Martin - Troyes", h1="Le Bistrot Martin", body='<a href="/mentions-legales">Mentions légales</a>'), legal=legal)
    dns_calls: list = []
    monkeypatch.setattr(sitefinder, "dns_resolves", lambda ds, timeout=4.0: dns_calls.append(ds) or [d for d in ds if d == "bistrot-martin.fr"])
    calls: list[str] = []

    def down(query):
        calls.append(query)
        raise errors.SearchUnavailable("tous les moteurs sont en cooldown", errors.SEARCH_RATE_LIMIT)
    camp = add_campaign(conn)
    stats = runner.run_campaign(conn, camp, search=down, fetch=web)
    assert "cooldown" in stats["waiting"] and len(calls) == 1                               # les moteurs saturés ne sont plus sollicités ensuite
    assert len(dns_calls) <= 2 and stats["search_skipped"] >= 2                             # au plus : un site deviné trouvé AVANT la panne, puis la fiche en panne
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects WHERE website_status='NOT_FOUND'")["n"] == 0   # jamais « non trouvé » faute de moteur
    waiting = db.fetch_all(conn, "SELECT pipeline_stage, attempts FROM local_prospects WHERE website_status IS NULL AND do_not_contact=0")
    assert len(waiting) >= 3 and {r["pipeline_stage"] for r in waiting} <= {"DISCOVERED", "WEBSITE_SEARCH"}   # rien de conclu, aucune erreur imputée :
    assert all(r["attempts"] == 0 for r in waiting)                                         # toujours dans TODO (website_status NULL)
    assert db.fetch_one(conn, "SELECT status FROM local_campaigns WHERE id=%s", (camp["id"],))["status"] == "running"
    stats = rerun(conn, camp, search=search, fetch=web)                                     # les moteurs sont revenus : tout est repris
    assert "waiting" not in stats and stats["remaining"] == 0 and stats["search_skipped"] == 0
    assert state(conn, "11111111100011")["website_status"] == "CONFIRMED"
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects WHERE website_status IS NULL AND do_not_contact=0")["n"] == 0


def test_osm_une_seule_requete_par_campagne_correspondance_et_site_verifie(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    web.add_site("chez-dupont-pains.fr", html(title="Chez Dupont", h1="Chez Dupont", body='<a href="/mentions-legales">ML</a><p>5 rue Voltaire 10000 Troyes</p>'),
                 legal=html(title="Mentions", body="<p>BOULANGERIE DUPONT SIRET 222 222 222 00011 5 rue Voltaire 10000 Troyes</p>"))
    calls: list[str] = []
    els = [{"type": "node", "id": 1, "lat": 48.292, "lon": 4.08, "tags": {"name": "Boulangerie Dupont", "website": "https://chez-dupont-pains.fr", "phone": "+33 3 25 00 11 22", "shop": "bakery"}},
           {"type": "node", "id": 2, "lat": 48.2921, "lon": 4.0801, "tags": {"name": "Tabac du coin", "website": "https://tabac.fr"}},           # même lieu, autre nom : ignoré
           {"type": "node", "id": 3, "lat": 45.0, "lon": 5.0, "tags": {"name": "Boulangerie Dupont", "website": "https://loin.fr"}}]                 # même nom, trop loin
    monkeypatch.setattr(osm, "ENABLED", True)
    monkeypatch.setattr(osm, "_transport", overpass(els, calls))
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=FakeSearch(), fetch=web)                       # la recherche web ne trouve RIEN : seul OSM fournit le site
    assert len(calls) == 1                                                                # UNE requête pour toute la campagne
    p = state(conn, "22222222200011")
    om = json.loads(p["osm_data"])
    assert om["osm_id"] == "n1" and om["distance_m"] < 50 and om["website"] == "https://chez-dupont-pains.fr"
    assert p["website_status"] == "CONFIRMED" and any(e["code"] == "structured_source" for e in json.loads(p["website_evidence"])["evidence"])
    assert state(conn, "11111111100011")["osm_data"] is None                              # aucun rapprochement sans nom compatible
    assert {r["source"] for r in db.fetch_all(conn, "SELECT source FROM local_prospect_sources WHERE prospect_id=%s", (p["id"],))} >= {"sirene", "osm", "websearch"}
    # cache : une 2e campagne dans la même zone ne rappelle PAS Overpass
    camp2 = add_campaign(conn)
    runner.run_campaign(conn, camp2, search=FakeSearch(), fetch=web)
    assert len(calls) == 1


def test_contact_via_osm_pour_un_site_non_trouve(env, conn, monkeypatch):
    """Sans site confirmé, OSM reste la seule source de contact possible : un prospect « site non trouvé » peut ainsi franchir le seuil « À contacter »."""
    install_api(monkeypatch)
    web, search = web_and_search()
    els = [{"type": "node", "id": 5, "lat": 48.292, "lon": 4.08, "tags": {"name": "Boulangerie Dupont", "phone": "+33 3 25 00 11 22"}}]
    monkeypatch.setattr(osm, "ENABLED", True)
    monkeypatch.setattr(osm, "_transport", overpass(els))
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    p = state(conn, "22222222200011")
    assert p["website_status"] == "NOT_FOUND" and p["phone"] == "0325001122"
    ev = json.loads(p["contact_evidence"])
    assert ev[0]["via"] == "osm" and ev[0]["source"] == "OpenStreetMap" and float(p["phone_confidence"]) < 0.6
    assert p["category"] in ("A_CONTACTER", "A_EXAMINER")           # un contact (même faible) permet de dépasser le plafond « sans contact »


def test_contact_osm_backfille_les_prospects_deja_traites(env, conn, monkeypatch):
    """Un prospect déjà « site non trouvé » AVANT ce correctif (jamais recontrôlé pour un contact) est repris au passage suivant."""
    install_api(monkeypatch)
    web, search = web_and_search()
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    p = state(conn, "22222222200011")
    assert p["website_status"] == "NOT_FOUND" and p["phone"] is None and p["contact_discovered_at"] is None
    db.execute(conn, "UPDATE local_prospects SET provenance = JSON_REMOVE(provenance, '$.contacts') WHERE id=%s", (p["id"],))   # donnée d'avant le correctif
    els = [{"type": "node", "id": 5, "lat": 48.292, "lon": 4.08, "tags": {"name": "Boulangerie Dupont", "email": "boulangerie.dupont@gmail.com"}}]
    monkeypatch.setattr(osm, "ENABLED", True)
    monkeypatch.setattr(osm, "_transport", overpass(els))
    cid = db.fetch_one(conn, "SELECT campaign_id FROM local_prospect_campaigns WHERE prospect_id=%s", (p["id"],))["campaign_id"]
    db.execute(conn, "UPDATE local_campaigns SET status='queued', stats='{}' WHERE id=%s", (cid,))
    conn.commit()
    runner.run_campaign(conn, db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (cid,)), search=search, fetch=web)
    after = state(conn, "22222222200011")
    assert after["email"] == "boulangerie.dupont@gmail.com" and after["website_status"] == "NOT_FOUND"    # toujours pas de site : jamais inventé


def test_contact_depuis_l_extrait_d_un_annuaire_quand_aucun_site_n_est_trouve(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    search.results['"BOULANGERIE DUPONT" "TROYES"'] = [Hit("https://www.pagesjaunes.fr/pros/2", "Boulangerie Dupont Troyes",
                                                          "Boulangerie Dupont, 5 rue Voltaire 10000 Troyes — 03 25 00 11 22")]
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    p = state(conn, "22222222200011")
    assert p["website_status"] == "NOT_FOUND" and p["phone"] == "0325001122"                 # l'annuaire n'est toujours PAS pris pour le site
    ev = json.loads(p["contact_evidence"])[0]
    assert ev["via"] == "search_snippet" and "pagesjaunes.fr" in ev["source"]
    assert json.loads(p["provenance"])["contacts"]["via"] == ["search_snippet"]


def test_backfill_requete_de_contact_une_seule_fois(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=web)
    pid = state(conn, "22222222200011")["id"]
    db.execute(conn, "UPDATE local_prospects SET provenance = JSON_REMOVE(provenance, '$.contacts') WHERE id=%s", (pid,))
    search.results['"BOULANGERIE DUPONT" TROYES téléphone'] = [Hit("https://www.118712.fr/x", "Boulangerie Dupont Troyes", "Troyes 10000 — 06 12 34 56 78")]
    search.queries.clear()
    db.execute(conn, "UPDATE local_campaigns SET status='queued' WHERE id=%s", (camp["id"],))
    conn.commit()
    runner.run_campaign(conn, db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp["id"],)), search=search, fetch=web)
    assert search.queries == ['"BOULANGERIE DUPONT" TROYES téléphone'] and state(conn, "22222222200011")["phone"] == "0612345678"
    search.queries.clear()
    db.execute(conn, "UPDATE local_campaigns SET status='queued' WHERE id=%s", (camp["id"],))
    conn.commit()
    runner.run_campaign(conn, db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp["id"],)), search=search, fetch=web)
    assert search.queries == []                                                                # jamais refaite


def test_osm_indisponible_n_est_pas_bloquant_et_est_journalise(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    monkeypatch.setattr(osm, "ENABLED", True)
    monkeypatch.setattr(osm, "_transport", httpx.MockTransport(lambda req: httpx.Response(429, text="slow down")))
    camp = add_campaign(conn)
    stats = runner.run_campaign(conn, camp, search=search, fetch=web)
    assert "osm_error" in stats and db.fetch_one(conn, "SELECT status FROM local_campaigns WHERE id=%s", (camp["id"],))["status"] == "done"
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_events WHERE category=%s AND source='osm'", (errors.SOURCE_UNAVAILABLE,))["n"] == 1


def test_telephone_osm_sert_de_repli_signale_comme_tel(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    els = [{"type": "node", "id": 9, "lat": 48.30, "lon": 4.09, "tags": {"name": "Salon Jolie", "phone": "03 25 44 55 66"}}]
    web.pages["http://salon-jolie.fr/mentions-legales"] = (200, html(title="Mentions", body="<p>SALON JOLIE - SIRET 777 777 777 00011 - 8 rue des Ormes 10000 Troyes</p>"))
    monkeypatch.setattr(osm, "ENABLED", True)
    monkeypatch.setattr(osm, "_transport", overpass(els))
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=web)
    p = state(conn, "77777777700011")
    ev = json.loads(p["contact_evidence"])
    phone = next(e for e in ev if e["kind"] == "phone")
    assert phone["via"] == "osm" and phone["source"] == "OpenStreetMap" and float(p["phone_confidence"]) < 0.6           # source signalée, confiance moyenne
    assert json.loads(p["provenance"])["phone"]["source"] == "OpenStreetMap"


# ── traçabilité, domaine partagé, établissements ─────────────────────────────────────────────────────────
def test_tracabilite_provenance_et_chaines_partagees(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=web)
    prov = json.loads(state(conn, "11111111100011", "provenance")["provenance"])
    assert prov["website"]["source"] == "searxng" and prov["website"]["url"] == "https://www.bistrot-martin.fr/" and prov["website"]["strategies"]
    assert prov["phone"]["url"].startswith("https://www.bistrot-martin.fr/") and prov["contacts"]["found"] is True
    p = state(conn, "11111111100011")
    assert p["contact_source_url"] and p["contact_discovered_at"] and float(p["contact_confidence"]) >= 0.7 and p["data_confidence_score"] and p["commercial_potential_score"]


def test_deux_etablissements_du_meme_siren_une_seule_fiche(env, conn, monkeypatch):
    def handler(req):
        if req.url.host == "recherche-entreprises.api.gouv.fr":
            u = unit("555000111", "BOULANGERIE DUO", [etab("55500011100011", street="1 RUE ZOLA"), etab("55500011100029", street="9 AVENUE PASTEUR")])
            return httpx.Response(200, json={"results": [u] if req.url.params["page"] == "1" else [], "total_pages": 1})
        return httpx.Response(200, json={"results": []})
    from worker.local import http
    monkeypatch.setattr(http, "_transport", httpx.MockTransport(handler))
    camp = add_campaign(conn)
    stats = runner.run_campaign(conn, camp, search=FakeSearch(), fetch=FakeWeb())
    rows = db.fetch_all(conn, "SELECT siret FROM local_prospects ORDER BY id")
    assert [r["siret"] for r in rows] == ["55500011100011"] and stats["same_company"] == 1          # même patron, même site : UNE fiche


# ── conformité et reset ─────────────────────────────────────────────────────────────────────────────────
def test_ne_plus_contacter_survit_au_reset_et_bloque_la_recreation(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    db.execute(conn, "INSERT INTO local_do_not_contact (siret, reason) VALUES ('11111111100011', 'opposition')")
    db.execute(conn, "INSERT INTO local_bad_sites (siret, domain) VALUES ('11111111100011', 'mauvais.fr')")
    conn.commit()
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=web)
    assert state(conn, "11111111100011")["category"] == "IGNORER"
    reset.reset_database(conn)
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_do_not_contact")["n"] == 1 and db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_bad_sites")["n"] == 1
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects")["n"] == 0
    rerun(conn, camp, search=search, fetch=web)                                              # redécouverte après reset
    p = state(conn, "11111111100011")
    assert p["do_not_contact"] == 1 and p["status"] == "DO_NOT_CONTACT" and p["category"] == "IGNORER" and p["website_status"] is None


def test_reset_apercu_ne_modifie_rien_et_liste_les_nouvelles_tables(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    before = {t: db.fetch_one(conn, f"SELECT COUNT(*) AS n FROM `{t}`")["n"] for t in reset.LOCAL_TABLES}
    counts = reset.preview(conn)
    assert {t: counts[t] for t in reset.LOCAL_TABLES} == before and before["local_prospects"] > 0
    assert {t: db.fetch_one(conn, f"SELECT COUNT(*) AS n FROM `{t}`")["n"] for t in reset.LOCAL_TABLES} == before
    assert "local_bad_sites" not in counts and "local_do_not_contact" not in counts and "local_site_feedback" not in counts


def test_metriques_de_passage_ecrites(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    m = db.fetch_one(conn, "SELECT * FROM local_run_metrics")
    assert m["processed"] >= 4 and m["confirmed"] == 2 and m["not_found"] >= 1 and m["duration_ms"] >= 0 and m["searches"] >= 4


def test_osm_reponse_plafonnee_est_signalee(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    monkeypatch.setattr(osm, "MAX_ELEMENTS", 2)
    els = [{"type": "node", "id": i, "lat": 45.0 + i, "lon": 5.0, "tags": {"name": f"Lieu {i}", "website": "https://x.fr"}} for i in range(2)]
    monkeypatch.setattr(osm, "ENABLED", True)
    monkeypatch.setattr(osm, "_transport", overpass(els))
    stats = runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    assert stats["osm_truncated"] is True                                                    # zone dense : on ne prétend pas avoir tout vu
