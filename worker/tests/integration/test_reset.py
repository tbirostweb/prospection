"""Remise à zéro (`python -m worker.reset`) sur un vrai MySQL : ce qui part, ce qui reste, et Telegram contre un faux serveur HTTP."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from worker import alerts, db, reset, telegram_poll
from worker.local import store


def _n(conn, table):
    return db.fetch_one(conn, f"SELECT COUNT(*) AS n FROM `{table}`")["n"]


def _populate(conn):
    cid = db.execute(conn, "INSERT INTO local_campaigns (name, city, activities, status, stats, coverage) VALUES ('Troyes','Troyes',%s,'done',%s,%s)",
                     (json.dumps(["restaurants"]), json.dumps({"a": 1}), json.dumps({"partial": False})))
    pid = db.execute(conn, "INSERT INTO local_prospects (siret, fingerprint, company_name) VALUES ('12345678900011','f','A')")
    db.execute(conn, "INSERT INTO local_prospect_campaigns (prospect_id, campaign_id) VALUES (%s,%s)", (pid, cid))
    db.execute(conn, "INSERT INTO local_prospect_sources (prospect_id, source) VALUES (%s,'sirene')", (pid,))
    db.execute(conn, "INSERT INTO local_http_cache (cache_key, body) VALUES ('k','{}')")
    db.execute(conn, "INSERT INTO local_do_not_contact (siret, reason) VALUES ('12345678900011','a demandé')")
    db.execute(conn, "INSERT INTO local_bad_sites (siret, domain) VALUES ('12345678900011','mauvais.fr')")
    db.execute(conn, "INSERT INTO local_site_feedback (siret, kind, url) VALUES ('12345678900011','found_site','https://bon.fr/')")
    db.execute(conn, "INSERT INTO local_engine_health (engine, health) VALUES ('brave', 80)")
    db.execute(conn, "INSERT INTO local_events (category) VALUES ('SITE_TIMEOUT')")
    db.execute(conn, "INSERT INTO local_run_metrics (processed) VALUES (3)")
    conn.commit()
    return cid, pid


def test_apercu_ne_modifie_rien(env, conn):
    _populate(conn)
    before = {t: _n(conn, t) for t in reset.LOCAL_TABLES + reset.TECH_TABLES}
    counts = reset.preview(conn)
    assert counts == before and counts["local_prospects"] == 1
    assert {t: _n(conn, t) for t in before} == before
    assert "local_do_not_contact" not in counts and "local_bad_sites" not in counts and "local_campaigns" not in counts


def test_remise_a_zero_garde_les_decisions_et_la_configuration(env, conn):
    cid, pid = _populate(conn)
    max_id = db.fetch_one(conn, "SELECT MAX(id) AS m FROM local_prospects")["m"]
    deleted = reset.reset_database(conn)
    assert deleted["local_prospects"] == 1
    assert [_n(conn, t) for t in reset.LOCAL_TABLES + reset.TECH_TABLES] == [0] * 8
    assert _n(conn, "local_do_not_contact") == 1 and _n(conn, "local_bad_sites") == 1 and _n(conn, "local_site_feedback") == 1     # tes décisions : JAMAIS effacées
    c = db.fetch_one(conn, "SELECT status, stats, coverage, name FROM local_campaigns WHERE id=%s", (cid,))
    assert (c["status"], c["stats"], c["coverage"], c["name"]) == ("idle", None, None, "Troyes")                              # configuration gardée
    assert store.is_do_not_contact(conn, siret="12345678900011")
    new = db.execute(conn, "INSERT INTO local_prospects (siret, fingerprint, company_name) VALUES ('99999999900011','g','B')")
    assert new > max_id                                                                                                       # les identifiants ne repartent pas de 1


def test_keep_local_ne_remet_a_zero_que_l_etat_technique(env, conn):
    _populate(conn)
    reset.reset_database(conn, keep_local=True)
    assert _n(conn, "local_prospects") == 1 and _n(conn, "local_engine_health") == 0 and _n(conn, "local_events") == 0
    assert db.fetch_one(conn, "SELECT status FROM local_campaigns")["status"] == "done"


class FakeTelegram:
    """Faux serveur : `sendMessage` renvoie un identifiant ; `deleteMessage` n'accepte que les messages récents (< 48 h)."""

    def __init__(self, marker: int = 100, oldest_deletable: int = 96):
        self.calls: list[tuple[str, dict]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):                                              # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
                method = self.path.rsplit("/", 1)[1]
                outer.calls.append((method, body))
                result = [{"update_id": 40}, {"update_id": 41}] if method == "getUpdates" else {"message_id": marker} if method == "sendMessage" else True
                ok = not (method == "deleteMessage" and body["message_id"] < oldest_deletable)
                data = json.dumps({"ok": ok, "result": result}).encode()
                self.send_response(200 if ok else 400)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def test_telegram_repart_de_zero(monkeypatch, tmp_path):
    api = FakeTelegram()
    offset, alert_state = tmp_path / "offset.json", tmp_path / "alert_state.json"
    offset.write_text('{"offset": 7}')
    alert_state.write_text('{"x": 1}')
    monkeypatch.setattr(reset, "TELEGRAM_API_BASE", api.url)
    monkeypatch.setattr(reset, "TELEGRAM_BOT_TOKEN", "123456789:TEST-fake-token")
    monkeypatch.setattr(reset, "TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(reset, "SWEEP_STOP_AFTER", 3)
    monkeypatch.setattr(reset.time, "sleep", lambda s: None)
    monkeypatch.setattr(telegram_poll, "OFFSET_FILE", str(offset))
    monkeypatch.setattr(alerts, "STATE_FILE", str(alert_state))
    try:
        res = reset.reset_telegram()
    finally:
        api.server.shutdown()
    assert res["configured"] and res["messages_deleted"] == 5 and res["updates_skipped"] == 2 and res["state_files_removed"] == 2
    assert json.loads(offset.read_text())["offset"] == 42 and not alert_state.exists()
    assert [b["message_id"] for m, b in api.calls if m == "deleteMessage"] == [100, 99, 98, 97, 96, 95, 94, 93]
    assert "TEST-fake-token" not in json.dumps(res)


def test_telegram_non_configure_ne_fait_rien(monkeypatch, tmp_path):
    monkeypatch.setattr(reset, "TELEGRAM_BOT_TOKEN", "")
    assert reset.reset_telegram(state_files=(str(tmp_path / "absent.json"),)) == {"configured": False, "updates_skipped": 0, "messages_deleted": 0, "state_files_removed": 0}


def test_demande_depuis_l_application(env, conn, monkeypatch):
    import io
    cid, _ = _populate(conn)
    sent = []
    monkeypatch.setattr("worker.notify.send_text", lambda t: sent.append(t) or True)
    assert reset.process_request(conn, lock_fn=lambda: io.StringIO()) is None                                                 # aucune demande : rien
    assert _n(conn, "local_prospects") == 1
    uid = db.execute(conn, "INSERT INTO users (email) VALUES ('t@t.fr')")
    db.execute(conn, "INSERT INTO settings (user_id, skey, value_json) VALUES (%s, 'reset_request', %s)", (uid, json.dumps({"relaunch": True})))
    conn.commit()
    assert reset.process_request(conn, lock_fn=lambda: None) is None                                                          # passage en cours : on attend
    assert _n(conn, "local_prospects") == 1
    out = reset.process_request(conn, lock_fn=lambda: io.StringIO())
    assert out["prospects"] == 1 and out["relaunched"] == 1 and sent
    assert _n(conn, "local_prospects") == 0 and _n(conn, "local_do_not_contact") == 1 and _n(conn, "local_bad_sites") == 1
    assert _n(conn, "local_engine_health") == 1                                                                               # cooldowns des moteurs gardés
    assert db.fetch_one(conn, "SELECT status FROM local_campaigns WHERE id=%s", (cid,))["status"] == "queued"
    assert db.fetch_one(conn, "SELECT 1 AS x FROM settings WHERE skey='reset_request'") is None                               # demande consommée
    assert db.fetch_one(conn, "SELECT 1 AS x FROM settings WHERE skey='reset_last'")
