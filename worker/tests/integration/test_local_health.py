"""Santé, funnel, mesures par campagne, dégradations et auto-audit (lecture seule)."""
from __future__ import annotations

import json

from worker import audit_system, db
from worker.local import errors, health, monitor, runner

from .test_local_pipeline import add_campaign, install_api, web_and_search


def _run(conn, monkeypatch):
    install_api(monkeypatch)
    web, search = web_and_search()
    camp = add_campaign(conn)
    runner.run_campaign(conn, camp, search=search, fetch=web)
    return camp


def _snapshot(conn):
    tables = [r[next(iter(r))] for r in db.fetch_all(conn, "SHOW TABLES")]
    return {t: db.fetch_one(conn, f"SELECT COUNT(*) AS n FROM `{t}`")["n"] for t in tables}


def test_funnel_montre_ou_l_on_perd_des_prospects(env, conn, monkeypatch):
    camp = _run(conn, monkeypatch)
    f = {s["key"]: s["count"] for s in health.funnel(conn, camp["id"])}
    assert f["discovered"] == 5 and f["eligible"] == 5                      # chaîne et agence web bannies dès la découverte
    assert f["searched"] == 5 and f["identified"] == 2 and f["confirmed"] == 2 and f["audited"] == 0 and f["contacts"] >= 1   # audit désactivé par défaut (LOCAL_AUDIT)
    assert f["discovered"] >= f["eligible"] >= f["searched"] >= f["identified"] >= f["audited"]
    assert all(s["label"] for s in health.funnel(conn))


def test_mesures_par_campagne(env, conn, monkeypatch):
    camp = _run(conn, monkeypatch)
    m = health.campaign_metrics(conn, camp["id"])
    assert m["discovered"] == 5 and m["confirmed"] == 2 and m["not_found"] >= 1 and m["audits"] == 0 and m["errors"] == 0
    assert m["qualified"] == m["tres_bon"] + m["a_contacter"] + m["a_examiner"] and m["contacts"] >= 1
    assert m["total_time_s"] >= 0 and m["avg_time_per_prospect_s"] is not None


def test_sante_des_moteurs_et_statistiques_d_erreurs(env, conn):
    db.execute(conn, "INSERT INTO local_engine_health (engine, health, requests, failures, consecutive_failures, cooldown_until, last_error) "
                     "VALUES ('google', 20, 10, 6, 3, UTC_TIMESTAMP() + INTERVAL 1 HOUR, 'CAPTCHA'), ('bing', 90, 10, 0, 0, NULL, NULL)")
    errors.record(conn, errors.SEARCH_CAPTCHA, "google")
    errors.record(conn, errors.SEARCH_CAPTCHA, "google")
    errors.record(conn, errors.SITE_DNS_ERROR, "site")
    conn.commit()
    rows = {r["engine"]: r for r in health.engines_status(conn)}
    assert rows["google"]["state"] == "cooldown" and rows["google"]["error_rate"] == 60.0 and rows["bing"]["state"] == "ok"
    assert {e["category"]: e["n"] for e in health.error_stats(conn)} == {"SEARCH_CAPTCHA": 2, "SITE_DNS_ERROR": 1}


def test_degradation_taux_de_sites_trouves_qui_s_effondre(env, conn):
    def metric(hours_ago, processed, found, searches=40, empty=2):
        db.execute(conn, "INSERT INTO local_run_metrics (started_at, processed, confirmed, searches, searches_empty) VALUES (UTC_TIMESTAMP() - INTERVAL %s HOUR, %s, %s, %s, %s)",
                   (hours_ago, processed, found, searches, empty))
    for h in (30, 50, 80, 120):                                              # les jours précédents : 60 % de sites trouvés
        metric(h, 20, 12)
    metric(2, 20, 1)                                                         # aujourd'hui : 5 %
    conn.commit()
    codes = {d["code"] for d in health.degradations(conn)}
    assert "SITE_RATE_DROP" in codes


def test_degradation_searxng_ne_renvoie_plus_rien(env, conn):
    for h in (30, 60, 100):
        db.execute(conn, "INSERT INTO local_run_metrics (started_at, processed, confirmed, searches, searches_empty) VALUES (UTC_TIMESTAMP() - INTERVAL %s HOUR, 20, 8, 40, 5)", (h,))
    db.execute(conn, "INSERT INTO local_run_metrics (started_at, processed, confirmed, searches, searches_empty) VALUES (UTC_TIMESTAMP() - INTERVAL 1 HOUR, 12, 0, 40, 39)")
    conn.commit()
    assert "SEARCH_EMPTY" in {d["code"] for d in health.degradations(conn)}


def test_pas_de_fausse_alerte_sans_base_de_comparaison(env, conn):
    db.execute(conn, "INSERT INTO local_run_metrics (started_at, processed, confirmed, searches, searches_empty) VALUES (UTC_TIMESTAMP(), 12, 0, 30, 30)")
    conn.commit()
    assert "SITE_RATE_DROP" not in {d["code"] for d in health.degradations(conn)}          # pas d'historique : on ne conclut pas


def test_tous_les_moteurs_en_cooldown_est_critique_et_alerte_partie(env, conn, monkeypatch):
    db.execute(conn, "INSERT INTO local_engine_health (engine, cooldown_until, consecutive_failures) VALUES ('google', UTC_TIMESTAMP() + INTERVAL 1 HOUR, 3), ('bing', UTC_TIMESTAMP() + INTERVAL 1 HOUR, 3)")
    conn.commit()
    sent = []
    monkeypatch.setattr("worker.alerts.alert", lambda sig, text: sent.append((sig, text)))
    monkeypatch.setattr("worker.db.connect", lambda: __import__("contextlib").nullcontext(conn))
    assert monitor.main() == 1
    assert sent and sent[0][0] == "local_ALL_ENGINES_DOWN" and "moteurs" in sent[0][1]


def test_budget_de_moteurs_epuise_n_est_pas_une_panne_critique(env, conn):
    """3 moteurs, tous à leur budget du jour (pas de cooldown, pas d'erreur) : dégradation visible, mais AVERTISSEMENT, jamais critique."""
    db.execute(conn, """INSERT INTO local_engine_health (engine, window_requests, window_start) VALUES
                        ('brave', 300, UTC_TIMESTAMP()), ('google cse', 300, UTC_TIMESTAMP()), ('duckduckgo', 300, UTC_TIMESTAMP())""")
    conn.commit()
    d = {p["code"]: p for p in health.degradations(conn)}
    assert d["ALL_ENGINES_BUDGET"]["level"] == "warning" and "pas une panne" in d["ALL_ENGINES_BUDGET"]["message"]
    assert "ALL_ENGINES_DOWN" not in d
    rows = {r["engine"]: r for r in health.engines_status(conn)}
    assert all(r["state"] == "budget" for r in rows.values())


def test_un_seul_moteur_reellement_en_panne_parmi_le_budget_reste_critique(env, conn):
    db.execute(conn, """INSERT INTO local_engine_health (engine, window_requests, window_start) VALUES ('brave', 300, UTC_TIMESTAMP()), ('duckduckgo', 300, UTC_TIMESTAMP())""")
    db.execute(conn, "INSERT INTO local_engine_health (engine, cooldown_until, consecutive_failures) VALUES ('google cse', UTC_TIMESTAMP() + INTERVAL 1 HOUR, 3)")
    conn.commit()
    assert "ALL_ENGINES_DOWN" in {p["code"] for p in health.degradations(conn)}


def test_alerte_edge_triggered_pas_de_rappel_tant_que_ca_persiste(env, conn, monkeypatch):
    db.execute(conn, "INSERT INTO local_engine_health (engine, cooldown_until, consecutive_failures) VALUES ('google', UTC_TIMESTAMP() + INTERVAL 1 HOUR, 3)")
    conn.commit()
    sent = []
    monkeypatch.setattr("worker.alerts.alert", lambda sig, text: sent.append(sig))
    monkeypatch.setattr("worker.db.connect", lambda: __import__("contextlib").nullcontext(conn))
    monitor.main()
    monitor.main()
    monitor.main()
    assert sent == ["local_ALL_ENGINES_DOWN"]                                              # une seule alerte malgré 3 passages, le problème persiste
    db.execute(conn, "UPDATE local_engine_health SET cooldown_until = UTC_TIMESTAMP() - INTERVAL 1 MINUTE")   # résolu
    conn.commit()
    monitor.main()
    db.execute(conn, "UPDATE local_engine_health SET cooldown_until = UTC_TIMESTAMP() + INTERVAL 1 HOUR")     # puis re-cassé
    conn.commit()
    monitor.main()
    assert sent == ["local_ALL_ENGINES_DOWN", "local_ALL_ENGINES_DOWN"]                    # une réapparition = une nouvelle alerte


def test_audit_systeme_est_en_lecture_seule_et_signale_les_problemes(env, conn, monkeypatch):
    camp = _run(conn, monkeypatch)
    db.execute(conn, "UPDATE local_prospects SET pipeline_stage='ERROR', error_category='SITE_TIMEOUT', next_retry_at=NULL WHERE siret='77777777700011'")
    db.execute(conn, "INSERT INTO local_engine_health (engine, cooldown_until, consecutive_failures, last_error, health) VALUES ('google', UTC_TIMESTAMP() + INTERVAL 1 HOUR, 3, 'CAPTCHA', 10)")
    db.execute(conn, "UPDATE local_campaigns SET status='running', last_run_at = UTC_TIMESTAMP() - INTERVAL 9 HOUR WHERE id=%s", (camp["id"],))
    conn.commit()
    before = _snapshot(conn)
    rows = audit_system.collect(conn)
    conn.commit()
    assert _snapshot(conn) == before                                                       # AUCUNE modification
    text = " | ".join(f"{r['section']}:{r['level']}:{r['message']}" for r in rows)
    assert "Prospects en erreur:critical" in text and "1 abandonné" in text
    assert "google" in text and "Campagnes:warning" in text and "sans progrès" in text
    assert json.dumps(rows, default=str)


def test_audit_systeme_sur_base_saine(env, conn, monkeypatch):
    _run(conn, monkeypatch)
    levels = {r["level"] for r in audit_system.collect(conn)}
    assert "critical" not in levels


def test_moniteur_ecrit_son_rapport_lisible_par_la_page_sante(env, conn, monkeypatch):
    db.execute(conn, "INSERT INTO local_engine_health (engine, cooldown_until, consecutive_failures) VALUES ('google', UTC_TIMESTAMP() + INTERVAL 1 HOUR, 3)")
    conn.commit()
    monkeypatch.setattr("worker.alerts.alert", lambda sig, text: None)
    monkeypatch.setattr("worker.db.connect", lambda: __import__("contextlib").nullcontext(conn))
    assert monitor.main() == 1
    row = db.fetch_one(conn, "SELECT at, problems FROM local_health_report WHERE id=1")
    assert row["at"] is not None and json.loads(row["problems"])[0]["code"] == "ALL_ENGINES_DOWN"
    monitor.main()                                                                        # idempotent : une seule ligne
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_health_report")["n"] == 1


def test_export_des_retours_utilisateur_en_cas_de_regression(env, conn):
    from worker.local import golden
    snap = json.dumps({"company_name": "BOULANGERIE X", "siret": "12345678900011", "city": "TROYES"})
    db.execute(conn, "INSERT INTO local_site_feedback (siret, kind, url, company) VALUES ('12345678900011','wrong_site','https://www.mauvais.fr/',%s)", (snap,))
    db.execute(conn, "INSERT INTO local_site_feedback (siret, kind, url, company) VALUES ('12345678900011','found_site','https://bon-site.fr/',%s)", (snap,))
    conn.commit()
    cases = golden.export(conn)["cases"]
    assert [(c["kind"], c["official_domain"], c["must_not_accept"]) for c in cases] == [("wrong_site", None, "mauvais.fr"), ("found_site", "bon-site.fr", None)]
    assert cases[0]["company"]["siret"] == "12345678900011"


def test_calibration_lecture_seule_et_prudente(env, conn):
    from worker.local import calibration
    for i in range(6):
        db.execute(conn, "INSERT INTO local_prospects (fingerprint, company_name, score_stage, prospect_score, status, feedback_reason) VALUES (%s,'A','FINAL',%s,%s,%s)",
                   (f"c{i}", 85, "CONTACTED" if i < 4 else "LOST", None if i < 4 else "wrong_site"))
    conn.commit()
    before = _snapshot(conn)
    r = calibration.report(conn)
    assert _snapshot(conn) == before
    top = r["bands"][0]
    assert top["judged"] == 6 and top["good"] == 4 and top["bad"] == 2 and top["good_rate"] is None and "trop peu" in top["note"]     # jamais 67 % sur 6 décisions
    assert r["reasons"] == [{"reason": "wrong_site", "n": 2}] and "AUCUN poids" in r["recommendation"]
