"""Telegram de la prospection locale : formatage HTML sûr, boutons de classement, envoi robuste, masquage du jeton."""
from __future__ import annotations

import re

from worker import notify, telegram_poll
from worker.telegram_poll import parse_callback

NASTY = {"id": 7, "company_name": "BOULANGERIE <script> & FILS *urgent* [SARL]", "trade_name": None, "activity_label": "Boulangeries, pâtisseries", "city": "Troyes",
         "distance_km": 2.35, "category": "A_CONTACTER", "prospect_score": 71, "website_status": "CONFIRMED", "website_confidence": 0.93,
         "website_url": "https://ex.com/p?a=1&b=2", "email": "contact_x@ex.com", "phone": "0325123456", "data_confidence_score": 82}


def _tags(text: str) -> list[str]:
    return re.findall(r"<[^>]+>", text)


def test_message_html_valide_et_echappe():
    msg = notify.format_prospect(NASTY, [{"label": "Pas de balise viewport <vite>", "severity": "high"}], ["+20 Potentiel site — modernisation HIGH & co"])
    tags = _tags(msg)
    assert set(tags) <= {"<b>", "</b>", "<i>", "</i>"} and tags.count("<b>") == tags.count("</b>")
    assert "<script>" not in msg.lower() and "&lt;script&gt;" in msg.lower() and "&amp;" in msg and "&lt;vite&gt;" in msg


def test_message_contenu_et_jamais_de_faux_besoin():
    msg = notify.format_prospect(NASTY)
    assert "71/100" in msg and "À contacter" in msg and "site confirmé (93 %)" in msg and "03 25 12 34 56" in msg and "2,4 km" in msg or "2,3 km" in msg
    assert "Fiabilité des données : 82/100" in msg and "Prospect à froid" in msg
    assert "cherche" not in msg.lower()                                                   # jamais « cherche un développeur »


def test_site_non_trouve_ne_pretend_pas_l_absence_certaine():
    msg = notify.format_prospect({**NASTY, "website_status": "NOT_FOUND", "website_url": None, "website_confidence": None, "email": None, "phone": None})
    assert "ne prouve pas l'absence de site" in msg and "aucune coordonnée trouvée" in msg


def test_donnees_absentes_n_inventent_rien():
    msg = notify.format_prospect({"id": 1, "company_name": "X"})
    assert "site pas encore recherché" in msg and "aucune coordonnée trouvée" in msg and "None" not in msg


def test_lien_vers_la_fiche_seulement_si_url_publique(monkeypatch):
    monkeypatch.setattr(notify, "APP_URL", "")
    assert "/local/" not in notify.format_prospect(NASTY)
    monkeypatch.setattr(notify, "APP_URL", "https://prospection.exemple.fr")
    assert "https://prospection.exemple.fr/local/7" in notify.format_prospect(NASTY)


def test_boutons_de_classement_et_callbacks_compacts():
    kb = notify.build_keyboard(9_999_999, has_site=True)
    data = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
    assert data == ["p:9999999:star", "p:9999999:no", "p:9999999:called", "p:9999999:wrong"]
    assert all(len(d.encode()) <= 64 for d in data)
    assert not any("wrong" in b["callback_data"] for row in notify.build_keyboard(1)["inline_keyboard"] for b in row)     # pas de « mauvais site » sans site
    reasons = [b["callback_data"] for row in notify.reasons_keyboard(123)["inline_keyboard"] for b in row]
    assert len(reasons) == 8 and all(len(d.encode()) <= 64 for d in reasons) and "p:123:r:wrong_site" in reasons


class _Resp:
    def __init__(self, status: int, body: dict):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


def test_repli_texte_brut_si_formatage_refuse(monkeypatch):
    monkeypatch.setattr(notify, "TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(notify, "TELEGRAM_CHAT_ID", "-100")
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)
        return _Resp(400, {"ok": False, "description": "Bad Request: can't parse entities"}) if "parse_mode" in json else _Resp(200, {"ok": True})

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    assert notify.send_text("<b>Titre</b> &amp; suite") is True
    assert len(calls) == 2 and "parse_mode" not in calls[1] and calls[1]["text"] == "Titre & suite"


def test_erreur_telegram_remontee(monkeypatch):
    monkeypatch.setattr(notify, "TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(notify, "TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(notify.httpx, "post", lambda url, json, timeout: _Resp(400, {"ok": False, "description": "Bad Request: chat not found"}))
    assert notify.send_text("x") is False and "chat not found" in notify.LAST_ERROR


def test_non_configure(monkeypatch):
    monkeypatch.setattr(notify, "TELEGRAM_BOT_TOKEN", "")
    assert notify.send_text("x") is False and "non défini" in notify.LAST_ERROR


def test_parse_callback():
    assert parse_callback("p:42:star") == (42, "star", None)
    assert parse_callback("p:7:no") == (7, "no", None)
    assert parse_callback("p:7:r:chain") == (7, "reason", "chain")
    for bad in ("p:42:delete_all", "x:1:star", "p:abc:star", "p:1", "p:1:r:inconnue", "j:42:interested", "", None):
        assert parse_callback(bad) is None


def test_jeton_masque_dans_les_logs(monkeypatch, capsys):
    import logging
    from worker import config

    token = "8947052718:AAF-jeton-de-test-secret"
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", token)
    assert token not in config.redact(f"POST https://api.telegram.org/bot{token}/getMe")
    fmt = config._RedactingFormatter("%(message)s")
    rec = logging.LogRecord("httpx", logging.INFO, __file__, 1, "HTTP Request: POST https://api.telegram.org/bot%s/getMe", (token,), None)
    assert token not in fmt.format(rec)
    try:
        raise RuntimeError(f"Client error for url https://api.telegram.org/bot{token}/x")
    except RuntimeError as exc:
        config._redacting_excepthook(type(exc), exc, exc.__traceback__)
    assert token not in capsys.readouterr().err


def test_httpx_ne_logue_plus_les_url_en_info():
    import logging
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_variantes_de_chat_id():
    from worker.telegram_check import chat_id_candidates
    assert chat_id_candidates("1004481306191") == ["-1004481306191"]
    assert chat_id_candidates("4481306191") == ["-4481306191", "-1004481306191"]
    assert chat_id_candidates("-1004481306191") == [] and chat_id_candidates("") == [] and chat_id_candidates("abc") == []


# ─── Après un tap ───────────────────────────────────────────────────────────
MSG = {"message_id": 77, "chat": {"id": 1234}}


def _calls(monkeypatch, fail_delete: bool = False):
    calls = []

    def fake(method, payload):
        calls.append((method, payload))
        return {"ok": not (fail_delete and method == "deleteMessage")}
    monkeypatch.setattr(telegram_poll, "_api", fake)
    return calls


def test_pas_interesse_propose_les_raisons_sans_supprimer(monkeypatch):
    calls = _calls(monkeypatch)
    telegram_poll._after(MSG, "no", 5)
    assert [c[0] for c in calls] == ["editMessageReplyMarkup"]
    assert calls[0][1]["reply_markup"] == notify.reasons_keyboard(5)


def test_raison_choisie_supprime_l_alerte(monkeypatch):
    calls = _calls(monkeypatch)
    telegram_poll._after(MSG, "reason", 5)
    assert calls == [("deleteMessage", {"chat_id": 1234, "message_id": 77})]


def test_suppression_refusee_retire_au_moins_les_boutons(monkeypatch):
    calls = _calls(monkeypatch, fail_delete=True)
    telegram_poll._after(MSG, "reason", 5)
    assert [c[0] for c in calls] == ["deleteMessage", "editMessageReplyMarkup"] and calls[1][1]["reply_markup"] == {"inline_keyboard": []}


def test_bon_prospect_garde_le_message_sans_boutons(monkeypatch):
    calls = _calls(monkeypatch)
    telegram_poll._after(MSG, "star", 5)
    assert [c[0] for c in calls] == ["editMessageReplyMarkup"]


def test_message_sans_identifiant_ne_fait_rien(monkeypatch):
    calls = _calls(monkeypatch)
    telegram_poll._after({}, "no", 5)
    assert calls == []
