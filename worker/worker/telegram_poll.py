"""Traite les boutons des alertes Telegram de la prospection locale (classement depuis le téléphone : ⭐ 📞 🚫 + raison, ❌ Mauvais site).

Choix du *polling* (getUpdates) plutôt qu'un webhook : aucun port à exposer,
aucune route à sortir de l'authentification de l'app, rien à configurer côté
Traefik. Lancé par cron toutes les 5 minutes.

Sécurité (refus par défaut) : un tap n'est appliqué que s'il provient du chat configuré (TELEGRAM_CHAT_ID, obligatoire,
le chat du message doit être présent et identique) ET d'un utilisateur autorisé :
  * TELEGRAM_ALLOWED_USER_IDS (identifiants séparés par des virgules) s'il est défini ;
  * sinon, en discussion directe, l'utilisateur lui-même (son identifiant = celui du chat) ;
  * sinon (canal / groupe), un administrateur du chat (getChatAdministrators).
Rejeu : l'offset est enregistré (écriture atomique) AVANT d'appliquer chaque action — au pire un tap est perdu
en cas d'arrêt brutal, jamais appliqué deux fois. Si l'offset ne peut pas être enregistré, rien n'est appliqué.
"""
from __future__ import annotations

import json
import os

import httpx

from . import db, notify
from .local import feedback
from .config import TELEGRAM_API_BASE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, log

OFFSET_FILE = os.environ.get("TELEGRAM_OFFSET_STATE", "/app/logs/telegram_offset.json")


def allowed_users() -> set[str]:
    return {x.strip() for x in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if x.strip()}

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


def _save_offset(offset: int) -> bool:
    """Écriture atomique (fichier temporaire + rename) : jamais de fichier tronqué. Renvoie False en cas d'échec."""
    tmp = f"{OFFSET_FILE}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"offset": offset}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, OFFSET_FILE)
        return True
    except OSError as exc:
        log.error("[telegram] offset non enregistré (%s) : traitement interrompu pour éviter tout rejeu", exc)
        return False


def _admins() -> set[str] | None:
    """Identifiants des administrateurs du chat configuré (canal / groupe). None si l'API ne répond pas (=> refus)."""
    data = _api("getChatAdministrators", {"chat_id": TELEGRAM_CHAT_ID})
    if not data or not data.get("ok"):
        return None
    return {str((m.get("user") or {}).get("id")) for m in data.get("result", []) if (m.get("user") or {}).get("id")}


def authorized(cq: dict, admins=_admins) -> tuple[bool, str]:
    """(autorisé, motif). Refus par défaut : chat absent ou différent, auteur absent ou non autorisé."""
    chat_id = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
    if not TELEGRAM_CHAT_ID or not chat_id or chat_id != str(TELEGRAM_CHAT_ID):
        return False, "chat non autorisé"
    user = str((cq.get("from") or {}).get("id", "") or "")
    if not user:
        return False, "auteur inconnu"
    allow = allowed_users()
    if allow:
        return (user in allow), "auteur hors liste TELEGRAM_ALLOWED_USER_IDS"
    if not chat_id.startswith("-"):
        return (user == chat_id), "auteur différent du chat privé"
    ids = admins()
    return (ids is not None and user in ids), "auteur non administrateur du chat"


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

    admin_cache: dict[str, set[str] | None] = {}

    def admins_once() -> set[str] | None:
        if "ids" not in admin_cache:
            admin_cache["ids"] = _admins()
        return admin_cache["ids"]

    with db.connect() as conn:
        for upd in sorted(updates, key=lambda u: int(u.get("update_id", 0))):
            uid = int(upd.get("update_id", 0))
            if uid < offset:
                continue                                    # déjà traité (ne devrait pas arriver)
            # Accusé de réception AVANT l'action : un redémarrage ne rejoue jamais un tap.
            if not _save_offset(uid + 1):
                return
            offset = uid + 1
            cq = upd.get("callback_query")
            if not cq:
                continue

            ok, why = authorized(cq, admins_once)
            if not ok:
                log.warning("[telegram] tap ignoré : %s", why)
                if cq.get("id"):
                    _api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Non autorisé"})
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


if __name__ == "__main__":
    poll()
