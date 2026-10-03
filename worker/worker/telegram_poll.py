"""Traite les boutons des alertes Telegram de la prospection locale (classement depuis le téléphone : ⭐ 📞 🚫 + raison, ❌ Mauvais site).

Choix du *polling* (getUpdates) plutôt qu'un webhook : aucun port à exposer,
aucune route à sortir de l'authentification de l'app, rien à configurer côté
Traefik. Lancé par cron toutes les 5 minutes.

Sécurité : on n'accepte que les taps provenant du chat configuré
(TELEGRAM_CHAT_ID). Un inconnu qui trouverait le bot ne peut rien modifier.
"""
from __future__ import annotations

import json
import os

import httpx

from . import db, notify
from .local import feedback
from .config import TELEGRAM_API_BASE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, log

OFFSET_FILE = os.environ.get("TELEGRAM_OFFSET_STATE", "/app/logs/telegram_offset.json")

# Actions des boutons d'une alerte de prospect local : `p:<id>:<action>` ; 2e étape du 🚫 : `p:<id>:r:<raison>`.
ACTIONS = ("star", "no", "called", "wrong")


def _api(method: str, payload: dict) -> dict | None:
    try:
        resp = httpx.post(
            f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/{method}",
            json=payload, timeout=20,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        log.warning("[telegram] %s a échoué : %s", method, exc)
        return None


def _load_offset() -> int:
    try:
        with open(OFFSET_FILE, encoding="utf-8") as f:
            return int(json.load(f).get("offset", 0))
    except (OSError, ValueError, TypeError):
        return 0


def _save_offset(offset: int) -> None:
    try:
        with open(OFFSET_FILE, "w", encoding="utf-8") as f:
            json.dump({"offset": offset}, f)
    except OSError:
        pass


def parse_callback(data: str) -> tuple[int, str, str | None] | None:
    """`p:<id>:<action>` ou `p:<id>:r:<raison>` -> (id, action, raison). None si invalide."""
    parts = (data or "").split(":")
    if not parts or parts[0] != "p" or len(parts) not in (3, 4):
        return None
    try:
        pid = int(parts[1])
    except ValueError:
        return None
    if len(parts) == 4:
        return (pid, "reason", parts[3]) if parts[2] == "r" and parts[3] in feedback.REASONS else None
    return (pid, parts[2], None) if parts[2] in ACTIONS else None


def _apply(conn, pid: int, action: str, reason: str | None) -> str:
    fn = {"star": feedback.star, "called": feedback.called, "wrong": feedback.wrong_site}
    if action == "no":
        text = feedback.dislike(conn, pid)
    elif action == "reason":
        text = feedback.dislike(conn, pid, reason)
    else:
        text = fn[action](conn, pid)
    log.info("[telegram] prospect %s -> %s", pid, action)
    return text


def _after(msg: dict, action: str, pid: int) -> None:
    """Après un tap : ⭐ / 📞 / ❌ retirent les boutons ; 🚫 propose les RAISONS (2e étape) ; la raison choisie SUPPRIME l'alerte (conversation propre)."""
    if not msg.get("message_id"):
        return
    ref = {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"]}
    if action == "no":
        _api("editMessageReplyMarkup", {**ref, "reply_markup": notify.reasons_keyboard(pid)})
        return
    if action == "reason":
        res = _api("deleteMessage", ref)
        if res and res.get("ok"):
            return                                         # au-delà de 48 h, Telegram refuse : on retire au moins les boutons
    _api("editMessageReplyMarkup", {**ref, "reply_markup": {"inline_keyboard": []}})


def poll() -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.info("[telegram] non configuré, rien à traiter")
        return

    offset = _load_offset()
    data = _api("getUpdates", {
        "offset": offset, "timeout": 0, "allowed_updates": ["callback_query"],
    })
    if not data or not data.get("ok"):
        return

    updates = data.get("result", [])
    if not updates:
        return

    last_id = offset
    with db.connect() as conn:
        for upd in updates:
            last_id = max(last_id, int(upd.get("update_id", 0)))
            cq = upd.get("callback_query")
            if not cq:
                continue

            # Sécurité : seul le chat configuré peut agir.
            chat_id = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
            if chat_id and str(TELEGRAM_CHAT_ID) != chat_id:
                log.warning("[telegram] tap ignoré : chat non autorisé (%s)", chat_id)
                continue

            parsed = parse_callback(cq.get("data", ""))
            if not parsed:
                continue
            pid, action, reason = parsed
            try:
                text = _apply(conn, pid, action, reason)
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                log.error("[telegram] échec action %s sur prospect %s : %s", action, pid, exc)
                text = "Erreur"

            _api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": text})
            _after(cq.get("message") or {}, action, pid)

    _save_offset(last_id + 1)   # accuse réception : ces updates ne reviendront plus


if __name__ == "__main__":
    poll()
