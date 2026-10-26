"""Recherche d'e-mails sur un VRAI MySQL : requête dédiée « nom ville email » pendant la campagne, nouvelle recherche plus tard pour les fiches
sans e-mail (une fois par période), score recalculé quand l'adresse est trouvée ou ajoutée à la main. Moteurs simulés (aucune requête réelle)."""
from __future__ import annotations

from worker import db
from worker.local import emailsearch, runner
from worker.local.engines import Hit

from .test_local_pipeline import add_campaign, install_api, web_and_search

DUPONT = "22222222200011"
EMAIL_Q = '"BOULANGERIE DUPONT" TROYES email'
HIT = Hit("https://www.facebook.com/boulangeriedupont", "Boulangerie Dupont | Troyes", "Boulangerie à Troyes. E-mail : boulangerie.dupont@orange.fr")


def row(conn, siret):
    return db.fetch_one(conn, "SELECT * FROM local_prospects WHERE siret=%s", (siret,))


def test_recherche_dediee_pendant_la_campagne(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    search.results[EMAIL_Q] = [HIT]
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    p = row(conn, DUPONT)
    assert EMAIL_Q in search.queries and p["email"] == "boulangerie.dupont@orange.fr" and p["email_kind"] == "UNCERTAIN"
    assert '"email_search"' in p["provenance"] and row(conn, "11111111100011")["email"] == "contact@bistrot-martin.fr"   # trouvée sur le site


def test_nouvelle_recherche_plus_tard_une_fois_par_periode(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)          # aucune adresse publiée à ce moment-là
    assert row(conn, DUPONT)["email"] is None
    db.execute(conn, """UPDATE local_prospects SET provenance = JSON_SET(provenance, '$.email_search.checked_at', '2026-01-01T00:00:00Z')
                        WHERE siret=%s""", (DUPONT,))
    search.results[EMAIL_Q] = [HIT]                                                  # l'adresse est apparue depuis
    search.queries.clear()
    stats = emailsearch.run(conn, search, limit=10)
    assert stats["found"] == 1 and row(conn, DUPONT)["email"] == "boulangerie.dupont@orange.fr" and EMAIL_Q in search.queries
    search.queries.clear()
    assert emailsearch.run(conn, search, limit=10)["searched"] == 0 or EMAIL_Q not in search.queries   # pas refaite avant 30 jours


def test_attribution_exigee_et_opposition_respectee(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    db.execute(conn, "UPDATE local_prospects SET provenance = JSON_REMOVE(provenance, '$.email_search') WHERE siret=%s", (DUPONT,))
    search.results[EMAIL_Q] = [Hit("https://annuaire.example/x", "Boulangerie Martin à Reims", "contact : autre@orange.fr")]   # ni le nom ni la ville
    emailsearch.run(conn, search, limit=10)
    assert row(conn, DUPONT)["email"] is None
    db.execute(conn, "INSERT INTO local_do_not_contact (email, reason) VALUES ('boulangerie.dupont@orange.fr', 'refus')")
    db.execute(conn, "UPDATE local_prospects SET provenance = JSON_REMOVE(provenance, '$.email_search') WHERE siret=%s", (DUPONT,))
    search.results[EMAIL_Q] = [HIT]
    emailsearch.run(conn, search, limit=10)
    assert row(conn, DUPONT)["email"] is None                                       # adresse opposée : jamais enregistrée


def test_adresse_ajoutee_a_la_main_rescoree(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    db.execute(conn, """UPDATE local_prospects SET email='boulangerie.dupont@orange.fr', email_kind='UNCERTAIN', scored_at=NULL,
                        provenance = JSON_SET(provenance, '$.email_manual', JSON_OBJECT('at', '2026-10-26T12:00:00Z', 'pending', TRUE)) WHERE siret=%s""", (DUPONT,))
    stats = emailsearch.run(conn, search, limit=0)
    p = row(conn, DUPONT)
    assert stats["manual_rescored"] == 1 and p["scored_at"] is not None and '"pending": false' in p["provenance"]
    assert emailsearch.run(conn, search, limit=0)["manual_rescored"] == 0


def test_brouillons_deja_rediges_cherches_en_premier(env, conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    db.execute(conn, "UPDATE local_prospects SET email=NULL, provenance = JSON_REMOVE(COALESCE(provenance, JSON_OBJECT()), '$.email_search')")
    db.execute(conn, "UPDATE local_prospects SET draft_message='Bonjour…' WHERE siret=%s", (DUPONT,))
    ids = [r["id"] for r in emailsearch.candidates(conn, 10)]
    assert ids and ids[0] == row(conn, DUPONT)["id"]
