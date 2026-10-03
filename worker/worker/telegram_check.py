"""Diagnostic Telegram en une commande :  python -m worker.telegram_check

Vérifie, dans l'ordre, tout ce qui peut empêcher un message d'arriver, et
affiche l'erreur EXACTE renvoyée par Telegram avec la marche à suivre :
  1. variables TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID présentes ;
  2. jeton valide (getMe) ;
  3. chat joignable (getChat) ;
  4. envoi réel d'un message de test ;
  5. prospects locaux en attente de notification
"""
from __future__ import annotations

import json

import httpx

from . import db
from .config import TELEGRAM_API_BASE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, redact
from . import notify

# Erreur Telegram -> explication + correction
HINTS = [
    ("unauthorized", "Jeton invalide. Recopie-le depuis @BotFather (commande /mybots) "
                     "dans TELEGRAM_BOT_TOKEN, puis redéploie."),
    ("chat not found", "TELEGRAM_CHAT_ID incorrect, OU (discussion privée) tu n'as jamais "
                       "envoyé /start au bot, OU (canal) le bot n'est pas membre du canal."),
    ("not a member", "Le bot n'est pas dans le canal : ajoute-le comme administrateur."),
    ("not enough rights", "Le bot est dans le canal mais sans droit de publier : "
                          "donne-lui « Publier des messages » dans les droits admin."),
    ("have no rights", "Le bot n'a pas le droit de publier : donne-lui « Publier des "
                       "messages » dans les droits admin du canal."),
    ("blocked by the user", "Tu as bloqué le bot : débloque-le dans Telegram."),
    ("upgraded to a supergroup", "Le groupe est devenu un supergroupe : son ID a changé "
                                 "(il commence désormais par -100). Mets à jour TELEGRAM_CHAT_ID."),
]


def chat_id_candidates(chat_id: str) -> list[str]:
    """Variantes plausibles d'un chat_id mal recopié, par ordre de probabilité.

    - `1004481306191`  -> `-1004481306191` : signe moins oublié (cas typique) ;
    - `4481306191`     -> `-1004481306191` : ID court d'un lien t.me/c/4481306191/…
    """
    cid = (chat_id or "").strip()
    if not cid or cid.startswith("-") or not cid.isdigit():
        return []
    out = [f"-{cid}"]
    if not cid.startswith("100"):
        out.append(f"-100{cid}")
    return out


def _hint(err: str | None) -> str:
    low = (err or "").lower()
    for key, text in HINTS:
        if key in low:
            return text
    return "Erreur non répertoriée : voir le message ci-dessus."


def _api(method: str, payload: dict) -> tuple[bool, dict | str]:
    try:
        r = httpx.post(f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/{method}",
                       json=payload, timeout=15)
    except httpx.HTTPError as exc:
        return False, f"réseau : {exc}"
    try:
        body = r.json()
    except ValueError:
        return False, f"{r.status_code} {r.text[:200]}"
    if body.get("ok"):
        return True, body.get("result", {})
    return False, f"{r.status_code} {body.get('description')}"


def _ok(msg: str) -> None:
    print(redact(f"  ✅ {msg}"))


def _ko(msg: str, err: str | None = None) -> None:
    print(redact(f"  ❌ {msg}"))
    if err:
        print(redact(f"     Telegram : {err}"))
        print(f"     👉 {_hint(err)}")


def main() -> None:
    print("═══ Diagnostic Telegram ═══\n")

    print("1. Configuration")
    if not TELEGRAM_BOT_TOKEN:
        _ko("TELEGRAM_BOT_TOKEN est vide")
    else:
        _ok(f"TELEGRAM_BOT_TOKEN défini ({TELEGRAM_BOT_TOKEN[:6]}…, {len(TELEGRAM_BOT_TOKEN)} car.)")
    if not TELEGRAM_CHAT_ID:
        _ko("TELEGRAM_CHAT_ID est vide")
    else:
        _ok(f"TELEGRAM_CHAT_ID = {TELEGRAM_CHAT_ID}")
        cid = TELEGRAM_CHAT_ID.strip()
        if cid.isdigit() and cid.startswith("100") and len(cid) >= 13:
            print(f"  ⚠️  Ressemble à un ID de CANAL sans le signe moins (attendu : -{cid})")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("\n  👉 Renseigne les deux dans Dokploy → onglet Environment, puis REDÉPLOIE :")
        print("     les variables ne sont lues qu'à la création du conteneur.")
        print("     Guide pas à pas : TELEGRAM.md")
        return

    print("\n2. Jeton du bot (getMe)")
    ok, res = _api("getMe", {})
    if not ok:
        _ko("jeton refusé", str(res))
        return
    _ok(f"bot @{res.get('username')} reconnu")

    print("\n3. Chat de destination (getChat)")
    ok, res = _api("getChat", {"chat_id": TELEGRAM_CHAT_ID})
    if not ok:
        _ko("chat injoignable", str(res))
        for candidate in chat_id_candidates(TELEGRAM_CHAT_ID):
            ok2, res2 = _api("getChat", {"chat_id": candidate})
            if ok2:
                name = res2.get("title") or res2.get("first_name") or "?"
                print(f"\n  🎯 Trouvé ! Avec « {candidate} », le chat existe : {res2.get('type')} « {name} ».")
                print("     Corrige dans Dokploy → Environment :")
                print(f"       TELEGRAM_CHAT_ID={candidate}")
                print("     puis REDÉPLOIE (les variables sont figées à la création du conteneur).")
                return
        tried = chat_id_candidates(TELEGRAM_CHAT_ID)
        if tried:
            print(f"\n  Variantes testées sans succès : {', '.join(tried)}.")
        print("  👉 Pour un canal, Telegram répond aussi « chat not found » tant que le bot\n"
              "     n'en est pas ADMINISTRATEUR, même avec le bon ID : ajoute-le (droit\n"
              "     « Publier des messages ») puis relance cette commande.")
        return
    _ok(f"{res.get('type')} « {res.get('title') or res.get('first_name') or '?'} »")

    print("\n4. Envoi d'un message de test")
    if notify.send_text("✅ <b>Prospection</b> — test de connexion réussi.\n"
                        "Tu recevras ici les alertes, résumés et boutons de triage."):
        _ok("message envoyé : regarde ton Telegram")
    else:
        _ko("envoi refusé", notify.LAST_ERROR)
        return

    print("\n5. Prospects prêts à être notifiés")
    with db.connect() as conn:
        st = db.fetch_one(conn, """SELECT COUNT(*) AS n, SUM(notified_at IS NULL) AS waiting FROM local_prospects
                                   WHERE score_stage='FINAL' AND category IN ('TRES_BON','A_CONTACTER') AND do_not_contact=0 AND excluded_reason IS NULL""") or {}
    n, waiting = int(st.get("n") or 0), int(st.get("waiting") or 0)
    print(f"  • Prospects « À contacter » ou mieux : {n} · pas encore notifiés : {waiting}")
    if n == 0:
        print("  👉 Normal de ne rien recevoir : aucun prospect n'atteint « À contacter » (lance une campagne dans l'onglet Prospection locale).")
    elif waiting:
        print("  👉 Ils seront envoyés au prochain passage du worker (au plus quelques-uns par passage).")


if __name__ == "__main__":
    main()
