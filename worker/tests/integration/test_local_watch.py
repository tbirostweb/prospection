"""Veille des nouvelles entreprises : BODACC (créations) → SIRENE (établissement) → prospect « nouvelle entreprise » → alerte Telegram unique."""
from __future__ import annotations

import json

import httpx

from worker import db
from worker.local import http, notify as local_notify, runner, watch

from .test_local_pipeline import add_campaign, etab, install_api, unit, web_and_search


def new_unit(siren, name, **kw):
    """Réponse de /search pour un SIREN : `matching_etablissements` vide, l'établissement est le siège."""
    u = unit(siren, name, [])
    u["siege"] = etab(siren + "00017", **kw)
    u["matching_etablissements"] = []
    return u


UNITS = {
    "901000001": new_unit("901000001", "LA NOUVELLE TABLE", created="2026-09-01", street="3 RUE ZOLA"),                       # ✔ restaurant dans le rayon
    "901000002": new_unit("901000002", "BISTROT LOINTAIN", lat="45.76", lon="4.83"),                                        # ✘ à Lyon : hors rayon
    "901000003": new_unit("901000003", "GARAGE NOUVEAU", naf="45.20A"),                                                    # ✘ activité hors campagne
    "901000004": new_unit("901000004", "RESTO SANS COORDONNEES", lat=None, lon=None),                                      # ✘ position inconnue
    "901000005": {**new_unit("901000005", "RESTO DISCRET"), "statut_diffusion": "P"},                                     # ✘ non diffusable
}


def install_watch_api(monkeypatch, seen: list | None = None):
    def handler(req):
        if req.url.host == "bodacc-datadila.opendatasoft.com":
            where = req.url.params["where"]
            assert 'familleavis_lib="Créations"' in where and 'numerodepartement="10"' in where
            recs = [{"dateparution": "2026-09-10", "familleavis_lib": "Créations", "registre": [s]} for s in list(UNITS) + ["111111111"]]
            return httpx.Response(200, json={"results": recs})
        if req.url.path.endswith("/search"):
            q = req.url.params["q"]
            if seen is not None:
                seen.append(q)
            return httpx.Response(200, json={"results": [UNITS[q]] if q in UNITS else []})
        raise AssertionError(f"appel inattendu : {req.url}")
    monkeypatch.setattr(http, "_transport", httpx.MockTransport(handler))


def _watched_campaign(conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=web)                    # la zone est déjà explorée (dont 111111111, déjà connu)
    db.execute(conn, "UPDATE local_campaigns SET watch=1, watch_every_days=30, last_watch_at=NULL WHERE id=%s", (camp["id"],))
    conn.commit()
    return db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp["id"],)), web, search


def test_veille_ne_garde_que_les_creations_dans_la_zone_et_les_activites(env, conn, monkeypatch):
    camp, _web, _search = _watched_campaign(conn, monkeypatch)
    seen: list[str] = []
    install_watch_api(monkeypatch, seen)
    st = watch.watch_campaign(conn, camp)
    assert st["new"] == 1 and st["departments"] == ["10"]
    assert "111111111" not in seen                                                 # déjà connue : jamais réinterrogée
    p = db.fetch_one(conn, "SELECT * FROM local_prospects WHERE siren='901000001'")
    assert p["new_business_at"] is not None and json.loads(p["bodacc"])["kind"] == "creation" and p["website_status"] is None
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects WHERE siren LIKE %s", ("90100000%",))["n"] == 1
    c = db.fetch_one(conn, "SELECT status, last_watch_at FROM local_campaigns WHERE id=%s", (camp["id"],))
    assert c["status"] == "queued" and c["last_watch_at"] is not None           # la campagne est relancée : site, contacts et score suivront
    assert not watch.due_campaigns(conn)                                         # prochaine veille dans 30 jours seulement


def test_alerte_telegram_unique_une_fois_evaluee(env, conn, monkeypatch):
    camp, web, search = _watched_campaign(conn, monkeypatch)
    install_watch_api(monkeypatch)
    watch.watch_campaign(conn, camp)
    texts: list[str] = []
    assert local_notify.notify_new_businesses(conn, send_text=lambda t: texts.append(t) or True)["sent"] == 0    # pas encore évaluée : on attend
    install_api(monkeypatch)
    runner.run_campaign(conn, db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp["id"],)), search=search, fetch=web)
    assert local_notify.notify_new_businesses(conn, send_text=lambda t: texts.append(t) or True)["sent"] == 1
    assert len(texts) == 1 and "nouvelle(s) entreprise(s)" in texts[0] and "La Nouvelle Table" in texts[0] and "créée le 01/09/2026" in texts[0]
    assert "signal, pas une demande" in texts[0]
    assert local_notify.notify_new_businesses(conn, send_text=lambda t: texts.append(t) or True)["sent"] == 0    # jamais deux fois
    assert len(texts) == 1


def test_telegram_en_panne_rien_n_est_perdu(env, conn, monkeypatch):
    camp, _w, _s = _watched_campaign(conn, monkeypatch)
    install_watch_api(monkeypatch)
    watch.watch_campaign(conn, camp)
    db.execute(conn, "UPDATE local_prospects SET new_business_at = UTC_TIMESTAMP() - INTERVAL 13 HOUR WHERE new_business_at IS NOT NULL")
    conn.commit()
    assert local_notify.notify_new_businesses(conn, send_text=lambda t: False)["sent"] == 0
    assert db.fetch_one(conn, "SELECT new_business_notified_at FROM local_prospects WHERE siren='901000001'")["new_business_notified_at"] is None
    assert local_notify.notify_new_businesses(conn, send_text=lambda t: True)["sent"] == 1     # évaluée ou non, annoncée après 12 h


def test_veille_desactivee_ou_pas_encore_due(env, conn, monkeypatch):
    camp, _w, _s = _watched_campaign(conn, monkeypatch)
    assert [c["id"] for c in watch.due_campaigns(conn)] == [camp["id"]]
    db.execute(conn, "UPDATE local_campaigns SET last_watch_at = UTC_TIMESTAMP() - INTERVAL 8 DAY, watch_every_days=7 WHERE id=%s", (camp["id"],))
    assert [c["id"] for c in watch.due_campaigns(conn)] == [camp["id"]]
    db.execute(conn, "UPDATE local_campaigns SET watch=0 WHERE id=%s", (camp["id"],))
    assert not watch.due_campaigns(conn)


def test_ne_plus_contacter_jamais_reajoute_par_la_veille(env, conn, monkeypatch):
    camp, _w, _s = _watched_campaign(conn, monkeypatch)
    db.execute(conn, "INSERT INTO local_do_not_contact (siren, reason) VALUES ('901000001', 'opposition')")
    conn.commit()
    install_watch_api(monkeypatch)
    assert watch.watch_campaign(conn, camp)["new"] == 0
