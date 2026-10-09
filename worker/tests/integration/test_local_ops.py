"""Exploitation : rescore au démarrage, résumés Telegram, healthcheck, notification en fin de passage."""
from __future__ import annotations

import json

from worker import db, digest, healthcheck, notify
from worker.local import rescore, runner

from .test_local_pipeline import add_campaign, install_api, web_and_search


def _run(conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    return runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)


def test_rescore_applique_les_nouveaux_poids_sans_reseau(env, conn, monkeypatch):
    _run(conn, monkeypatch)
    before = {r["id"]: r["prospect_score"] for r in db.fetch_all(conn, "SELECT id, prospect_score FROM local_prospects WHERE score_stage='FINAL'")}
    uid = db.fetch_one(conn, "SELECT id FROM users ORDER BY id LIMIT 1")["id"]
    db.execute(conn, "INSERT INTO settings (user_id, skey, value_json) VALUES (%s,'local_weights',%s) ON DUPLICATE KEY UPDATE value_json=VALUES(value_json)",
               (uid, json.dumps({"proximity": 0, "freshness": 0, "site_potential": 60})))
    conn.commit()
    n = rescore.rescore_all(conn)
    after = {r["id"]: r["prospect_score"] for r in db.fetch_all(conn, "SELECT id, prospect_score FROM local_prospects WHERE score_stage='FINAL'")}
    assert n >= len(before) and after != before and set(after) == set(before)


def test_resume_quotidien_et_hebdomadaire(env, conn, monkeypatch, telegram):
    _run(conn, monkeypatch)
    monkeypatch.setattr("worker.db.connect", lambda: __import__("contextlib").nullcontext(conn))
    digest.daily()
    digest.weekly()
    assert len(telegram.texts) == 2
    assert "Ta journée de prospection" in telegram.texts[0] and "Les 3 meilleurs à contacter" in telegram.texts[0]
    assert "Tournée proposée" in telegram.texts[0] and "google.com/maps/dir/" in telegram.texts[0]
    assert "Bilan de la semaine" in telegram.texts[1] and "Entreprises découvertes" in telegram.texts[1]


def test_healthcheck_ok_sur_base_saine_et_critique_si_base_injoignable(env, conn, monkeypatch, capsys):
    monkeypatch.setattr("worker.db.connect", lambda: __import__("contextlib").nullcontext(conn))
    monkeypatch.setattr(healthcheck, "_probe", lambda url: (False, "ConnectError"))            # SearXNG absent : avertissement, pas critique
    assert healthcheck.main() == 0
    assert "SearXNG" in capsys.readouterr().out

    def boom():
        raise RuntimeError("MySQL injoignable")
    monkeypatch.setattr("worker.db.connect", boom)
    assert healthcheck.main() == 1


def test_fin_de_passage_notifie_les_prospects_qualifies(env, conn, monkeypatch, telegram):
    install_api(monkeypatch)
    web, search = web_and_search()
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=web)
    db.execute(conn, "UPDATE local_prospects SET category='A_CONTACTER', score_stage='FINAL' WHERE website_status IN ('CONFIRMED','PROBABLE')")
    db.execute(conn, "UPDATE local_campaigns SET status='queued' WHERE id=%s", (camp["id"],))
    conn.commit()
    monkeypatch.setattr("worker.db.connect", lambda: __import__("contextlib").nullcontext(conn))
    monkeypatch.setattr(runner, "run_campaign", lambda *a, **k: {})                            # le passage lui-même est testé ailleurs
    runner.run_pending()
    assert len(telegram.prospects) >= 2
    assert notify.CATEGORY_ICON["A_CONTACTER"] in notify.format_prospect(telegram.prospects[0][0])


def test_resume_du_matin_relances_dues_meilleurs_et_tournee(env, conn, monkeypatch, telegram):
    from datetime import datetime, timedelta
    from worker.local import today
    camp = add_campaign(conn)
    now = datetime(2026, 9, 27, 7, 15)

    def add(siret, name, **over):
        row = {"siret": siret, "fingerprint": siret, "company_name": name, "city": "TROYES", "activity_key": "plombiers", "latitude": 48.3,
               "longitude": 4.08, "prospect_score": 70, "category": "A_CONTACTER", "status": "QUALIFIED", **over}
        pid = db.execute(conn, f"INSERT INTO local_prospects ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})", tuple(row.values()))
        db.execute(conn, "INSERT INTO local_prospect_campaigns VALUES (%s,%s)", (pid, camp["id"]))
    add("1", "PLOMBERIE A", status="CONTACTED", contacted_at=now - timedelta(days=4), phone="0325000000")          # relance 1 due
    add("2", "PLOMBERIE B", status="CONTACTED", contacted_at=now - timedelta(days=4), followups=1)                  # relance 2 à J+10 : pas encore
    add("3", "PLOMBERIE C", status="CONTACTED", contacted_at=now - timedelta(days=25), followups=2)                 # à clore
    ready = dict(pipeline_stage="DONE", deep_enriched_at=now, phone="0325111111", website_status="CONFIRMED")
    add("4", "COUVERTURE D", prospect_score=82, category="TRES_BON", buy_signals=json.dumps([{"code": "takeover", "label": "Reprise d'un commerce (BODACC)"}]), **ready)
    add("7", "INCOMPLET G", prospect_score=90, category="TRES_BON")                                                  # fiche pas prête : jamais proposée
    add("5", "CHAINE E", is_chain=1, **ready)
    add("6", "PAS BON F", category="A_EXAMINER", **ready)
    conn.commit()
    due = today.followups(conn, now)
    assert [(r["company_name"], r["due"]) for r in due] == [("PLOMBERIE C", "Sans réponse depuis 25 jours"), ("PLOMBERIE A", "Relance 1 (J+3)")]
    top = today.top_prospects(conn)
    assert [r["company_name"] for r in top] == ["COUVERTURE D"] and top[0]["signal"].startswith("Reprise")
    tour = today.tour(conn)
    assert [p["company_name"] for p in tour["stops"]] == ["COUVERTURE D"] and "waypoints=48.300000,4.080000" in tour["maps_url"]
    monkeypatch.setattr("worker.db.connect", lambda: __import__("contextlib").nullcontext(conn))
    monkeypatch.setattr(today, "followups", lambda c: due)
    digest.daily()
    text = telegram.texts[-1]
    assert "Relances du jour (2)" in text and "☎ 03 25 00 00 00 · tôt (7 h 30" in text and "Couverture D" in text
    assert "Chaine E" not in text and "Pas Bon F" not in text and "Incomplet G" not in text


def test_creneaux_d_appel_identiques_web_et_worker():
    from pathlib import Path
    from worker.local import today
    ts = (Path(__file__).resolve().parents[3] / "web/lib/local.ts").read_text(encoding="utf-8")
    for keys, when in today.TIME_BY_ACTIVITY:
        assert f'"{when}"' in ts and all(f'"{k}"' in ts for k in keys)
    assert f'"{today.DEFAULT_TIME}"' in ts and "FOLLOWUP_DAYS = [3, 10]" in ts and "NO_REPLY_DAYS = 21" in ts
