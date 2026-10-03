"""Probabilité d'achat sur un vrai MySQL : l'apprentissage survit à la remise à zéro, BODACC hors du runner, relances."""
from __future__ import annotations

import json

import httpx

from worker import db, reset
from worker.local import http, learning, runner

from .test_local_pipeline import add_campaign


def _prospect(conn, siret, cid, **over):
    row = {"siret": siret, "siren": siret[:9], "fingerprint": siret, "company_name": f"Entreprise {siret}", "activity_key": "plombiers", "city": "Troyes",
           "website_status": "NOT_FOUND", **over}
    pid = db.execute(conn, f"INSERT INTO local_prospects ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})", tuple(row.values()))
    db.execute(conn, "INSERT INTO local_prospect_campaigns (prospect_id, campaign_id) VALUES (%s,%s)", (pid, cid))
    return pid


def test_resultats_archives_et_conserves_apres_remise_a_zero(env, conn):
    cid = add_campaign(conn)["id"]
    db.execute(conn, "INSERT IGNORE INTO users (email) VALUES ('learning@t.fr')")
    for i in range(6):
        _prospect(conn, f"1000000000{i:04d}", cid, status="REPLIED" if i < 3 else "LOST", contacted_at="2026-09-01")
    for i in range(6):
        _prospect(conn, f"2000000000{i:04d}", cid, activity_key="bars", status="LOST", contacted_at="2026-09-01")
    _prospect(conn, "30000000000001", cid, status="TO_CONTACT")                                     # pas encore un résultat
    conn.commit()
    model, rescore = learning.refresh(conn)
    assert model["outcomes"] == 12 and model["successes"] == 3 and model["active"] and rescore
    assert model["features"]["metier:plombiers"]["points"] > 0 > model["features"]["metier:bars"]["points"]
    stored = json.loads(db.fetch_one(conn, "SELECT value_json FROM settings WHERE skey='local_learning'")["value_json"])
    assert stored["fingerprint"] == model["fingerprint"]
    assert learning.refresh(conn)[1] is False                                                      # rien de neuf : pas de nouveau rescore

    reset.reset_database(conn)
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects")["n"] == 0
    after, _ = learning.refresh(conn)
    assert after["outcomes"] == 12 and after["features"]["metier:plombiers"]["points"] == model["features"]["metier:plombiers"]["points"]


def test_le_runner_n_interroge_plus_bodacc(env, conn, monkeypatch):
    """BODACC n'est plus revérifié par SIREN pendant une campagne (débit réservé au site et aux contacts) ; la veille (watch.py) le garde."""
    from local_fixtures import FakeSearch, FakeWeb
    camp = add_campaign(conn, stats=json.dumps({"discovery_done": True}))
    a = _prospect(conn, "11111111100011", camp["id"], bodacc_checked_at="2020-01-01", phone="0325000000")
    conn.commit()
    asked = []

    def handler(req):
        asked.append(str(req.url))
        return httpx.Response(200, json={"results": []})
    monkeypatch.setattr(http, "_transport", httpx.MockTransport(handler))
    runner.run_campaign(conn, db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp["id"],)), search=FakeSearch(), fetch=FakeWeb())
    assert not any("bodacc" in u for u in asked)
    assert db.fetch_one(conn, "SELECT bodacc_checked_at FROM local_prospects WHERE id=%s", (a,))["bodacc_checked_at"].year == 2020
