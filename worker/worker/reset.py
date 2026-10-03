"""Remise à zéro de la prospection locale : repartir d'une application vide, sans toucher à ta configuration.

    python -m worker.reset                              # APERÇU : compte ce qui serait supprimé, ne modifie rien
    python -m worker.reset --yes                        # supprime les prospects locaux et leurs caches, remet les campagnes à zéro
    python -m worker.reset --yes --telegram             # + repart de zéro côté Telegram
    python -m worker.reset --yes --keep-local           # garde les prospects (ne remet à zéro que l'état technique : moteurs, événements, métriques)
    python -m worker.reset --pending                    # exécute la remise à zéro demandée depuis l'application (bouton Paramètres), cron chaque minute

SUPPRIMÉ : prospects, leurs sources et rattachements de campagne, caches HTTP ; état technique (santé des moteurs, événements, métriques de passage,
dernier rapport de surveillance). Les campagnes gardent leur configuration (statut remis à « idle »).
CONSERVÉ, TOUJOURS : la liste « ne plus contacter », les « mauvais sites » signalés et les retours « J'ai trouvé le site » (tes décisions), tes réglages
(poids, seuils, ville de référence), tes campagnes (configuration), les migrations.

Les identifiants de prospects ne repartent PAS de 1 (DELETE, pas TRUNCATE) : un vieux bouton Telegram ne vise jamais un prospect sans rapport.

Telegram (`--telegram`) : les taps en attente sont écartés, l'anti-spam des alertes et le décalage de lecture sont réinitialisés, et le bot
TENTE de supprimer les messages récents du chat. Limite de Telegram : un message de plus de 48 h ne peut pas être supprimé par un bot.
"""
from __future__ import annotations

import argparse
import json
import os
import time

import httpx

from . import alerts, db, telegram_poll
from .config import TELEGRAM_API_BASE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, log

# Prospects et caches partent ; campagnes (configuration), « ne plus contacter », « mauvais sites » et retours utilisateur sont CONSERVÉS —
# une entreprise qui a demandé à ne plus être contactée ne doit jamais redevenir contactable à cause d'une remise à zéro.
LOCAL_TABLES = ["local_prospects", "local_prospect_campaigns", "local_prospect_sources", "local_http_cache"]
TECH_TABLES = ["local_engine_health", "local_events", "local_run_metrics", "local_health_report"]
SWEEP_MAX_IDS = 400          # nombre maximal de messages Telegram récents tentés
SWEEP_STOP_AFTER = 40        # échecs consécutifs (messages > 48 h ou déjà supprimés) avant d'arrêter


def _count(conn, table: str) -> int:
    try:
        return db.fetch_one(conn, f"SELECT COUNT(*) AS n FROM `{table}`")["n"]
    except Exception:  # noqa: BLE001 — table absente d'une très ancienne base : rien à supprimer
        conn.rollback()
        return 0


def preview(conn, keep_local: bool = False) -> dict:
    tables = ([] if keep_local else LOCAL_TABLES) + TECH_TABLES
    return {t: _count(conn, t) for t in tables}


def reset_database(conn, keep_local: bool = False, keep_engines: bool = False) -> dict:
    """Vide les prospects locaux (sauf `keep_local`) et l'état technique. Renvoie le nombre de lignes supprimées par table.
    `keep_engines` garde la santé des moteurs (cooldowns, budget du jour) : repartir de zéro ne doit pas re-déclencher leurs blocages."""
    deleted: dict[str, int] = {}
    if not keep_local:
        try:                                         # tes résultats (réponse, client, perdu…) sont gardés : l'apprentissage survit à la remise à zéro
            from .local import learning
            learning.archive(conn)
            conn.commit()
        except Exception:  # noqa: BLE001 — table absente avant migration
            conn.rollback()
    tables = ([] if keep_local else LOCAL_TABLES) + [t for t in TECH_TABLES if not (keep_engines and t == "local_engine_health")]
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        try:
            for t in tables:
                try:
                    cur.execute(f"DELETE FROM `{t}`")
                    deleted[t] = cur.rowcount
                except Exception:  # noqa: BLE001
                    deleted[t] = 0
            if not keep_local:
                cur.execute("UPDATE local_campaigns SET status='idle', last_run_at=NULL, last_finished_at=NULL, last_error=NULL, stats=NULL, coverage=NULL")
        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    conn.commit()
    return deleted


def _api(method: str, payload: dict) -> dict | None:
    try:
        r = httpx.post(f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/{method}", json=payload, timeout=20)
        return r.json() if r.status_code < 500 else None
    except (httpx.HTTPError, ValueError):
        return None


def reset_telegram(state_files: tuple[str, ...] | None = None) -> dict:
    """Repart de zéro côté Telegram. Ne touche pas à la base. Renvoie un compte rendu (jamais le jeton)."""
    out = {"configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID), "updates_skipped": 0, "messages_deleted": 0,
           "state_files_removed": 0}
    for path in state_files or (telegram_poll.OFFSET_FILE, alerts.STATE_FILE):
        try:
            os.remove(path)
            out["state_files_removed"] += 1
        except OSError:
            pass
    if not out["configured"]:
        return out

    # 1) Taps en attente écartés : on lit le dernier update et on avance le décalage juste après.
    res = _api("getUpdates", {"offset": -1, "timeout": 0, "allowed_updates": ["callback_query"]})
    last = [int(u.get("update_id", 0)) for u in ((res or {}).get("result") or [])]
    if last:
        telegram_poll._save_offset(max(last) + 1)
        out["updates_skipped"] = len(last)

    # 2) Message repère : son identifiant borne le balayage des messages récents (Telegram numérote les messages du chat).
    sent = _api("sendMessage", {"chat_id": TELEGRAM_CHAT_ID, "text": "🧹 Prospection : remise à zéro en cours…"})
    marker = ((sent or {}).get("result") or {}).get("message_id")
    if not isinstance(marker, int):
        return out
    misses = 0
    for message_id in range(marker, max(0, marker - SWEEP_MAX_IDS), -1):
        res = _api("deleteMessage", {"chat_id": TELEGRAM_CHAT_ID, "message_id": message_id})
        if res and res.get("ok"):
            out["messages_deleted"] += 1
            misses = 0
        else:
            misses += 1
            if misses >= SWEEP_STOP_AFTER:
                break
        time.sleep(0.04)                                    # bien sous la limite de Telegram (~30 requêtes / s)
    _api("sendMessage", {"chat_id": TELEGRAM_CHAT_ID, "text": "✅ Prospection repart de zéro."})
    return out


REQUEST_KEY = "reset_request"        # posé par le bouton « Tout effacer » (Paramètres) ; hors préfixe local_ pour ne jamais être renvoyé par l'écran
RESULT_KEY = "reset_last"


def process_request(conn, lock_fn=None) -> dict | None:
    """Exécute la remise à zéro demandée depuis l'application, sous le verrou de la prospection locale.
    Renvoie le compte rendu, ou None (aucune demande, ou passage en cours : on réessaiera à la prochaine minute)."""
    row = db.fetch_one(conn, "SELECT user_id, value_json FROM settings WHERE skey=%s", (REQUEST_KEY,))
    if not row:
        return None
    req = row["value_json"]
    req = json.loads(req) if isinstance(req, (str, bytes)) else (req or {})
    lock = (lock_fn or _acquire_lock)()
    if lock is None:
        log.info("[reset] demande en attente : un passage est en cours")
        return None
    try:
        deleted = reset_database(conn, keep_engines=True)
        relaunched = 0
        if req.get("relaunch", True):
            with conn.cursor() as cur:
                relaunched = cur.execute("UPDATE local_campaigns SET status='queued' WHERE enabled=1")
        out = {"done_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "requested_at": req.get("requested_at"),
               "prospects": deleted.get("local_prospects", 0), "relaunched": relaunched}
        db.execute(conn, "DELETE FROM settings WHERE skey=%s", (REQUEST_KEY,))
        db.execute(conn, "INSERT INTO settings (user_id, skey, value_json) VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE value_json=VALUES(value_json)",
                   (row["user_id"], RESULT_KEY, json.dumps(out)))
        conn.commit()
    finally:
        lock.close()
    try:
        os.remove(alerts.STATE_FILE)                      # l'anti-spam repart aussi de zéro
    except OSError:
        pass
    log.info("[reset] remise à zéro depuis l'application : %s", out)
    try:
        from . import notify
        notify.send_text(f"🧹 <b>Prospection</b> — remise à zéro : {out['prospects']} entreprise(s) effacée(s)"
                         + (f", {relaunched} campagne(s) relancée(s)." if relaunched else "."))
    except Exception:  # noqa: BLE001 — Telegram ne bloque jamais la remise à zéro
        pass
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--yes", action="store_true", help="exécute réellement (sans cela : aperçu seulement)")
    ap.add_argument("--telegram", action="store_true", help="repart aussi de zéro côté Telegram")
    ap.add_argument("--keep-local", action="store_true", help="garde les prospects locaux (la liste « ne plus contacter » est TOUJOURS conservée)")
    ap.add_argument("--pending", action="store_true", help="exécute la remise à zéro demandée depuis l'application, s'il y en a une")
    args = ap.parse_args()

    if args.pending:
        with db.connect() as conn:
            process_request(conn)
        return
    with db.connect() as conn:
        counts = preview(conn, args.keep_local)
        print("Ce qui serait supprimé :" if not args.yes else "Suppression en cours :")
        for table, n in counts.items():
            print(f"  {table:<28} {n:>7} ligne(s)")
        print("\nToujours conservé : « ne plus contacter », « mauvais sites », retours, réglages, configuration des campagnes.")
        if not args.yes:
            print("\nAUCUNE modification. Pour exécuter : python -m worker.reset --yes [--telegram] [--keep-local]")
            return
        lock = _acquire_lock()
        if lock is None:
            raise SystemExit("Un passage du worker est en cours : réessaie dans quelques minutes (rien n'a été modifié).")
        try:
            deleted = reset_database(conn, args.keep_local)
        finally:
            lock.close()
        print(f"\nBase remise à zéro ({sum(deleted.values())} ligne(s) supprimée(s)). Réglages et campagnes conservés.")
        log.info("[reset] base remise à zéro : %s", deleted)
    if args.telegram:
        res = reset_telegram()
        print("Telegram :", "non configuré (variables TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID absentes)" if not res["configured"]
              else f"{res['messages_deleted']} message(s) supprimé(s) (Telegram n'efface pas au-delà de 48 h), "
                   f"{res['updates_skipped']} tap(s) en attente écarté(s)")
        print(f"  état local réinitialisé : {res['state_files_removed']} fichier(s)")


def _acquire_lock():
    from .local import runner
    return runner._acquire_lock()


if __name__ == "__main__":
    main()
