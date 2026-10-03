"""Telegram par HTTP RÉEL contre un faux serveur d'API local : le vrai client (`notify`, `telegram_poll`) envoie de vraies requêtes ; seul le serveur distant
est simulé (aucun jeton Telegram dans cet environnement). Ce que ça prouve : une alerte seulement pour un prospect « À contacter » ou mieux, une seule fois,
jamais pour « ne plus contacter » ; les boutons classent réellement le prospect. Ce que ça ne prouve pas : que Telegram accepte ces charges utiles."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from worker import alerts, db, notify, telegram_poll
from worker.local import notify as local_notify
from worker.local import runner

from .test_local_pipeline import add_campaign, install_api, prospect, web_and_search

TOKEN, CHAT = "123456789:TEST-fake-token", "42"


class FakeTelegramAPI:
    def __init__(self):
        self.requests: list[tuple[str, dict]] = []
        self.updates: list[dict] = []
        self.refuse_send = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):                                              # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
                assert self.path.startswith(f"/bot{TOKEN}/"), self.path
                method = self.path.rsplit("/", 1)[1]
                outer.requests.append((method, body))
                ok = not (outer.refuse_send and method == "sendMessage")
                result = outer.updates if method == "getUpdates" else {"message_id": 1000 + len(outer.requests)}
                data = json.dumps({"ok": ok, "result": result, **({} if ok else {"description": "Bad Request: chat not found"})}).encode()
                self.send_response(200 if ok else 400)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def sent(self) -> list[dict]:
        return [b for m, b in self.requests if m == "sendMessage"]

    def calls(self, method: str) -> list[dict]:
        return [b for m, b in self.requests if m == method]

    def close(self):
        self.server.shutdown()


@pytest.fixture
def conn(env_sans_telegram):
    with db.connect() as c:
        c.autocommit(True)
        yield c


@pytest.fixture
def tg_api(env_sans_telegram, monkeypatch, tmp_path):
    api = FakeTelegramAPI()
    for mod in (notify, telegram_poll):
        monkeypatch.setattr(mod, "TELEGRAM_API_BASE", api.url)
        monkeypatch.setattr(mod, "TELEGRAM_BOT_TOKEN", TOKEN)
        monkeypatch.setattr(mod, "TELEGRAM_CHAT_ID", CHAT)
    monkeypatch.setattr(alerts, "_load", lambda: {})
    monkeypatch.setattr(alerts, "_save", lambda state: None)
    monkeypatch.setattr(telegram_poll, "OFFSET_FILE", str(tmp_path / "offset.json"))
    yield api
    api.close()


def _qualify(conn, monkeypatch, n_extra: int = 0):
    """Une campagne complète, puis des prospects « À contacter » (le fixture de site le plus abouti + quelques ajoutés à la main)."""
    install_api(monkeypatch)
    web, search = web_and_search()
    runner.run_campaign(conn, add_campaign(conn), search=search, fetch=web)
    ids = [r["id"] for r in db.fetch_all(conn, "SELECT id FROM local_prospects WHERE website_status IN ('CONFIRMED','PROBABLE')")]
    db.execute(conn, "UPDATE local_prospects SET category='A_CONTACTER', score_stage='FINAL', prospect_score=70 WHERE id IN (%s)" % ",".join(map(str, ids)))
    for i in range(n_extra):
        db.execute(conn, """INSERT INTO local_prospects (fingerprint, company_name, city, category, score_stage, prospect_score, status)
                            VALUES (%s,%s,'TROYES','A_CONTACTER','FINAL',%s,'QUALIFIED')""", (f"extra{i}", f"ENTREPRISE {i}", 60 + i))
    return ids


def test_alerte_seulement_pour_un_prospect_qualifie_une_seule_fois(conn, monkeypatch, tg_api):
    ids = _qualify(conn, monkeypatch)
    res = local_notify.notify_qualified(conn)
    assert res["sent"] == len(ids) >= 2
    again = local_notify.notify_qualified(conn)
    assert again["sent"] == 0                                                          # jamais deux fois
    msgs = tg_api.sent()
    assert len(msgs) == len(ids) and all(m["chat_id"] == CHAT and m["parse_mode"] == "HTML" for m in msgs)
    buttons = [b["text"] for row in msgs[0]["reply_markup"]["inline_keyboard"] for b in row]
    assert buttons == ["⭐ Bon prospect", "🚫 Pas intéressé", "📞 Contacté", "❌ Mauvais site"]
    assert "Prospect à froid" in msgs[0]["text"] and "cherche" not in msgs[0]["text"].lower()
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects WHERE notified_at IS NOT NULL")["n"] == len(ids)


def test_jamais_d_alerte_pour_ne_plus_contacter_exclus_ou_faibles(conn, monkeypatch, tg_api):
    ids = _qualify(conn, monkeypatch)
    db.execute(conn, "UPDATE local_prospects SET do_not_contact=1, status='DO_NOT_CONTACT' WHERE id=%s", (ids[0],))
    db.execute(conn, "UPDATE local_prospects SET excluded_reason='agence web' WHERE id=%s", (ids[1],))
    db.execute(conn, "UPDATE local_prospects SET category='A_EXAMINER' WHERE id NOT IN (%s, %s)" % (ids[0], ids[1]))
    assert local_notify.notify_qualified(conn)["sent"] == 0 and tg_api.sent() == []


def test_plafond_par_passage_puis_un_seul_recapitulatif(conn, monkeypatch, tg_api):
    _qualify(conn, monkeypatch, n_extra=12)
    res = local_notify.notify_qualified(conn, limit=3)
    assert res["sent"] == 3 and res["waiting"] >= 10
    texts = [m["text"] for m in tg_api.sent()]
    assert sum("autre(s) prospect(s)" in t for t in texts) == 1 and len(texts) == 4
    assert local_notify.notify_qualified(conn, limit=3)["sent"] == 3                   # le passage suivant continue


def test_telegram_indisponible_rien_n_est_marque_notifie(conn, monkeypatch, tg_api):
    _qualify(conn, monkeypatch)
    tg_api.refuse_send = True
    res = local_notify.notify_qualified(conn)
    assert res["sent"] == 0 and res["failed"] == 1                                     # on n'insiste pas prospect par prospect
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_prospects WHERE notified_at IS NOT NULL")["n"] == 0
    tg_api.refuse_send = False
    assert local_notify.notify_qualified(conn)["sent"] >= 2                            # rien n'est perdu : tout part plus tard


def _tap(api, pid, action, chat=CHAT, uid=1):
    api.updates = [{"update_id": uid, "callback_query": {"id": f"cb{uid}", "data": f"p:{pid}:{action}", "message": {"message_id": 9, "chat": {"id": int(chat)}}}}]


def test_boutons_classent_reellement_le_prospect(conn, monkeypatch, tg_api):
    ids = _qualify(conn, monkeypatch)
    a, b = ids[0], ids[1]
    _tap(tg_api, a, "star", uid=1)
    telegram_poll.poll()
    assert db.fetch_one(conn, "SELECT status FROM local_prospects WHERE id=%s", (a,))["status"] == "TO_CONTACT"
    _tap(tg_api, a, "called", uid=2)
    telegram_poll.poll()
    p = db.fetch_one(conn, "SELECT status, contacted_at FROM local_prospects WHERE id=%s", (a,))
    assert p["status"] == "CONTACTED" and p["contacted_at"] is not None
    # 🚫 : d'abord écarté + choix de la raison, puis la raison est enregistrée et l'alerte supprimée
    tg_api.requests.clear()
    _tap(tg_api, b, "no", uid=3)
    telegram_poll.poll()
    assert db.fetch_one(conn, "SELECT status, response_status FROM local_prospects WHERE id=%s", (b,)) == {"status": "LOST", "response_status": "NOT_A_FIT"}
    assert tg_api.calls("editMessageReplyMarkup")[0]["reply_markup"] == notify.reasons_keyboard(b)
    tg_api.requests.clear()
    _tap(tg_api, b, "r:chain", uid=4)
    telegram_poll.poll()
    assert db.fetch_one(conn, "SELECT feedback_reason FROM local_prospects WHERE id=%s", (b,))["feedback_reason"] == "chain"
    assert tg_api.calls("deleteMessage") and tg_api.calls("answerCallbackQuery")[0]["text"].startswith("🚫")


def test_mauvais_site_par_telegram_dissocie_et_relance(conn, monkeypatch, tg_api):
    ids = _qualify(conn, monkeypatch)
    pid = ids[0]
    before = db.fetch_one(conn, "SELECT siret, canonical_domain FROM local_prospects WHERE id=%s", (pid,))
    _tap(tg_api, pid, "wrong")
    telegram_poll.poll()
    p = db.fetch_one(conn, "SELECT website_status, website_url, phone, email, pipeline_stage FROM local_prospects WHERE id=%s", (pid,))
    assert p == {"website_status": None, "website_url": None, "phone": None, "email": None, "pipeline_stage": "DISCOVERED"}
    assert db.fetch_one(conn, "SELECT domain FROM local_bad_sites WHERE siret=%s", (before["siret"],))["domain"] == before["canonical_domain"]
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_site_feedback WHERE kind='wrong_site'")["n"] == 1
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_campaigns WHERE status='queued'")["n"] == 1


def test_seul_le_chat_configure_peut_agir_et_le_ne_plus_contacter_est_intouchable(conn, monkeypatch, tg_api):
    ids = _qualify(conn, monkeypatch)
    _tap(tg_api, ids[0], "star", chat="999")                                          # chat inconnu
    telegram_poll.poll()
    assert db.fetch_one(conn, "SELECT status FROM local_prospects WHERE id=%s", (ids[0],))["status"] != "TO_CONTACT"
    db.execute(conn, "UPDATE local_prospects SET do_not_contact=1, status='DO_NOT_CONTACT' WHERE id=%s", (ids[0],))
    for uid, action in enumerate(("star", "called", "no", "wrong"), start=10):
        _tap(tg_api, ids[0], action, uid=uid)
        telegram_poll.poll()
    p = db.fetch_one(conn, "SELECT status, do_not_contact, website_status FROM local_prospects WHERE id=%s", (ids[0],))
    assert p["status"] == "DO_NOT_CONTACT" and p["do_not_contact"] == 1 and p["website_status"] is not None


def test_offset_enregistre_les_taps_ne_reviennent_pas(conn, monkeypatch, tg_api, tmp_path):
    ids = _qualify(conn, monkeypatch)
    _tap(tg_api, ids[0], "star", uid=41)
    telegram_poll.poll()
    assert json.loads((tmp_path / "offset.json").read_text())["offset"] == 42
    tg_api.requests.clear()
    telegram_poll.poll()
    assert tg_api.calls("getUpdates")[0]["offset"] == 42


def test_alerte_technique_de_degradation_est_envoyee_puis_throttlee(conn, tg_api, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_save", lambda state: sent.append(state))
    alerts.alert("local_TEST", "Prospection locale — test")
    assert tg_api.sent() and "Prospection" in tg_api.sent()[0]["text"]
