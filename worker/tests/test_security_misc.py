"""Masquage des secrets dans les journaux, dépendances bornées, autorisation Telegram (sans réseau)."""
from __future__ import annotations

import pathlib
import re

from worker import config, telegram_poll

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_cles_api_et_mots_de_passe_masques(monkeypatch):
    monkeypatch.setenv("LOCAL_SERPER_API_KEY", "serper-key-synthetique-123")
    monkeypatch.setenv("PAGESPEED_API_KEY", "pagespeed-synthetique-456")
    monkeypatch.setenv("APP_PASSWORD", "mot-de-passe-synthetique")
    monkeypatch.setenv("LOCAL_ENGINE_DAILY_BUDGET", "30000000")                # pas un secret : jamais masqué
    out = config.redact("url?key=serper-key-synthetique-123&k2=pagespeed-synthetique-456 pw=mot-de-passe-synthetique budget=30000000")
    assert "synthetique" not in out and out.count("***") == 3 and "30000000" in out


def test_dependances_bornees_et_verrouillees():
    py = (ROOT / "pyproject.toml").read_text()
    assert re.search(r'"selectolax>=[\d.]+,<1\.0"', py)          # selectolax 1.0 supprime selectolax.parser
    lock = (ROOT / "requirements.lock").read_text()
    for pkg in ("pymysql", "httpx", "selectolax"):
        assert re.search(rf"^{pkg}==[\d.]+ \\$", lock, re.M), pkg
    assert lock.count("--hash=sha256:") >= 9
    from worker.local import htmlinfo                           # l'import réel fonctionne avec la version verrouillée
    assert htmlinfo


def _cq(chat="42", user=42, message=True):
    cq = {"id": "1", "data": "p:1:star", "from": {"id": user}}
    if message:
        cq["message"] = {"message_id": 1, "chat": {"id": int(chat)}}
    return cq


def test_autorisation_telegram(monkeypatch):
    monkeypatch.setattr(telegram_poll, "TELEGRAM_CHAT_ID", "42")
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
    no_admins = lambda: set()                                                  # noqa: E731
    assert telegram_poll.authorized(_cq(), no_admins)[0] is True
    assert telegram_poll.authorized(_cq(user=7), no_admins)[0] is False      # mauvais auteur
    assert telegram_poll.authorized(_cq(chat="43", user=43), no_admins)[0] is False
    assert telegram_poll.authorized(_cq(message=False), no_admins)[0] is False
    assert telegram_poll.authorized({"id": "1", "message": {"chat": {"id": 42}}}, no_admins)[0] is False   # sans auteur
    monkeypatch.setattr(telegram_poll, "TELEGRAM_CHAT_ID", "-100123")
    assert telegram_poll.authorized(_cq(chat="-100123", user=5), lambda: {"5"})[0] is True
    assert telegram_poll.authorized(_cq(chat="-100123", user=6), lambda: {"5"})[0] is False
    assert telegram_poll.authorized(_cq(chat="-100123", user=5), lambda: None)[0] is False   # API admins indisponible => refus
    monkeypatch.setattr(telegram_poll, "TELEGRAM_CHAT_ID", "")
    assert telegram_poll.authorized(_cq(), no_admins)[0] is False
