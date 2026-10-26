"""Envoi automatique des e-mails de prospection :  python -m worker.local.outbox   (toutes les 10 min)

DÉSACTIVÉ par défaut : rien ne part tant que Réglages > Envoi automatique n'est pas activé. À chaque passage :
  1. la boîte de réception est lue (réponses, refus, adresses invalides) : une réponse arrête les relances AVANT tout nouvel envoi ;
  2. les séquences sans réponse depuis 14 jours sont abandonnées (plus aucun contact) ;
  3. si l'envoi est activé, dans les heures ouvrées (jours ouvrés, hors fériés), DNS vérifié et plafond du jour non atteint :
     UN seul e-mail part — une relance due (J+3, puis J+7 en réponse au premier message), sinon le prochain premier message en file.
Adresses de l'entreprise seulement (Gmail, Orange… publiées comme contact : réglage personal_emails, activé par défaut) ; chaque message contient la ligne d'opposition. Formulaires et Facebook : jamais.
"""
from __future__ import annotations

import datetime as dt
import json
import smtplib
import ssl
import sys

from .. import alerts, db
from ..config import env, log
from . import inbox, maildns, mailer
from .store import is_do_not_contact

DNS_TTL = dt.timedelta(hours=6)
MAX_FAILURES = 3


def mail_config() -> dict:
    """Configuration SMTP / IMAP (variables LOCAL_*), lue à chaque passage."""
    user = env("LOCAL_SMTP_USER", "") or ""
    host = env("LOCAL_SMTP_HOST", "") or ""
    return {"host": host, "port": int(env("LOCAL_SMTP_PORT", "587") or 587), "user": user, "password": env("LOCAL_SMTP_PASSWORD", "") or "",
            "from": (env("LOCAL_MAIL_FROM", "") or user).strip().lower(), "from_name": env("LOCAL_MAIL_FROM_NAME", "") or "",
            "imap_host": env("LOCAL_IMAP_HOST", "") or host, "imap_port": int(env("LOCAL_IMAP_PORT", "993") or 993),
            "dkim_selector": env("LOCAL_DKIM_SELECTOR", "") or None}


def missing_config(cfg: dict) -> list[str]:
    return [k for k, v in (("LOCAL_SMTP_HOST", cfg["host"]), ("LOCAL_SMTP_USER", cfg["user"]), ("LOCAL_SMTP_PASSWORD", cfg["password"]),
                           ("LOCAL_MAIL_FROM", "@" in cfg["from"])) if not v]


def smtp_send(msg, cfg: dict) -> None:
    ctx = ssl.create_default_context()
    if cfg["port"] == 465:
        server = smtplib.SMTP_SSL(cfg["host"], 465, timeout=30, context=ctx)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30)
        server.starttls(context=ctx)
    try:
        server.login(cfg["user"], cfg["password"])
        server.send_message(msg, from_addr=cfg["from"])
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            pass


def _setting(conn, key: str) -> dict:
    row = db.fetch_one(conn, "SELECT value_json FROM settings WHERE skey=%s LIMIT 1", (key,))
    v = row["value_json"] if row else None
    return (json.loads(v) if isinstance(v, (str, bytes)) else v) or {}


def _state(conn) -> dict:
    row = db.fetch_one(conn, "SELECT * FROM local_mail_state WHERE id=1") or {}
    checks = row.get("checks")
    row["checks"] = (json.loads(checks) if isinstance(checks, (str, bytes)) else checks) or {}
    return row


def _save_checks(conn, checks: dict) -> None:
    db.execute(conn, """INSERT INTO local_mail_state (id, checks, checks_at) VALUES (1, %s, UTC_TIMESTAMP())
                        ON DUPLICATE KEY UPDATE checks=VALUES(checks), checks_at=VALUES(checks_at)""", (json.dumps(checks, ensure_ascii=False),))


def _error(conn, text: str) -> None:
    log.warning("[mail] %s", text)
    db.execute(conn, """INSERT INTO local_mail_state (id, last_error, last_error_at) VALUES (1, %s, UTC_TIMESTAMP())
                        ON DUPLICATE KEY UPDATE last_error=VALUES(last_error), last_error_at=VALUES(last_error_at)""", (text[:255],))
    conn.commit()
    alerts.alert("local_mail_error", f"Envoi automatique — {text}")


def abandon(conn, now: dt.datetime) -> int:
    """Sans réponse 14 jours après le premier e-mail : perdu, plus aucun contact (comme « Sans réponse » dans le suivi manuel)."""
    limit = now.astimezone(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=mailer.NO_REPLY_DAYS)
    with conn.cursor() as cur:
        n = cur.execute("""UPDATE local_prospects SET auto_state='abandoned', status=IF(status='CONTACTED', 'LOST', status),
                           response_status=IF(status='LOST' AND response_status IS NULL, 'NO_ANSWER', response_status)
                           WHERE auto_state='active' AND contacted_at <= %s""", (limit,))
    conn.commit()
    return n


def next_task(conn, now: dt.datetime) -> tuple[str, dict] | None:
    """La prochaine chose à envoyer : relance due d'abord (elle a une date), sinon le premier message en file depuis le plus longtemps."""
    t = now.astimezone(dt.timezone.utc).replace(tzinfo=None)
    d1, d2 = mailer.FOLLOWUP_DAYS
    row = db.fetch_one(conn, """SELECT * FROM local_prospects WHERE auto_state='active' AND do_not_contact=0 AND status='CONTACTED'
                                AND ((followups=0 AND contacted_at <= %s)
                                  OR (followups=1 AND contacted_at <= %s AND (last_followup_at IS NULL OR last_followup_at <= %s)))
                                ORDER BY contacted_at LIMIT 1""",
                       (t - dt.timedelta(days=d1), t - dt.timedelta(days=d2), t - dt.timedelta(days=d2 - d1)))
    if row:
        return ("followup1" if int(row["followups"] or 0) == 0 else "followup2"), row
    row = db.fetch_one(conn, "SELECT * FROM local_prospects WHERE auto_state='queued' ORDER BY auto_queued_at, id LIMIT 1")
    return ("first", row) if row else None


def _stop(conn, p: dict, reason: str, state: str = "stopped") -> None:
    log.info("[mail] prospect %s retiré de l'envoi automatique : %s", p["id"], reason)
    db.execute(conn, "UPDATE local_prospects SET auto_state=%s WHERE id=%s", (state, p["id"]))
    conn.commit()


def send_one(conn, kind: str, p: dict, cfg: dict, sender: dict, now: dt.datetime, send=smtp_send, sent_copy=None, personal: bool = False) -> str:
    """Envoie UN e-mail. Renvoie 'sent', 'skipped' (prospect retiré) ou 'error' (problème du serveur : on réessaiera)."""
    from_name = cfg["from_name"] or sender.get("name") or ""
    if kind == "first":
        reason = mailer.ineligible(p, personal)
        email = (p.get("email") or "").strip().lower()
        if not reason and is_do_not_contact(conn, p.get("siret"), p.get("siren"), p.get("canonical_domain"), email):
            reason = "ne plus contacter"
        if not reason and (p.get("contacted_at") or db.fetch_one(conn, "SELECT 1 AS x FROM local_emails WHERE to_email=%s AND status<>'failed' LIMIT 1", (email,))):
            reason = "déjà contacté"
        if reason:
            _stop(conn, p, reason)
            return "skipped"
        subject, text, to, reply_to = p["draft_subject"].strip(), p["draft_message"], email, None
    else:
        first = db.fetch_one(conn, "SELECT * FROM local_emails WHERE prospect_id=%s AND kind='first' AND status='sent' ORDER BY sent_at LIMIT 1", (p["id"],))
        if not first or is_do_not_contact(conn, p.get("siret"), p.get("siren"), p.get("canonical_domain"), first["to_email"]):
            _stop(conn, p, "premier e-mail introuvable ou opposition")
            return "skipped"
        subject, to, reply_to = mailer.reply_subject(first["subject"]), first["to_email"], first["message_id"]
        text = mailer.followup_body(kind, p, first, now, from_name)
    msg = mailer.build_message(from_addr=cfg["from"], from_name=from_name, to=to, subject=subject, text=text, sender=sender, in_reply_to=reply_to)
    try:
        send(msg, cfg)
    except smtplib.SMTPRecipientsRefused as exc:
        db.execute(conn, """INSERT INTO local_emails (prospect_id, kind, to_email, subject, body, message_id, in_reply_to, status, error)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,'failed',%s)""", (p["id"], kind, to, subject[:255], text, msg["Message-ID"], reply_to, str(exc)[:255]))
        _stop(conn, p, "adresse refusée par le serveur", "bounced")
        return "skipped"
    except smtplib.SMTPDataError as exc:
        # Message refusé (contenu, filtre) : propre à ce prospect. Après MAX_FAILURES refus, il sort de l'envoi automatique.
        db.execute(conn, """INSERT INTO local_emails (prospect_id, kind, to_email, subject, body, message_id, in_reply_to, status, error)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,'failed',%s)""", (p["id"], kind, to, subject[:255], text, msg["Message-ID"], reply_to, str(exc)[:255]))
        conn.commit()
        fails = db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_emails WHERE prospect_id=%s AND status='failed'", (p["id"],))["n"]
        if fails >= MAX_FAILURES:
            _stop(conn, p, f"{fails} refus du serveur d'envoi", "failed")
        _error(conn, f"message refusé par le serveur ({exc.smtp_code})")
        return "error"
    except (smtplib.SMTPException, OSError) as exc:
        _error(conn, f"envoi impossible ({type(exc).__name__}) : {exc}")
        return "error"
    t = now.astimezone(dt.timezone.utc).replace(tzinfo=None)              # UTC naïf, comme toute la base
    db.execute(conn, """INSERT INTO local_emails (prospect_id, kind, to_email, subject, body, message_id, in_reply_to, status, sent_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,'sent',%s)""", (p["id"], kind, to, subject[:255], text, msg["Message-ID"], reply_to, t))
    if kind == "first":
        db.execute(conn, """UPDATE local_prospects SET status='CONTACTED', contacted_at=%s, last_contacted_at=%s, followups=0,
                            last_followup_at=NULL, auto_state='active' WHERE id=%s""", (t, t, p["id"]))
    else:
        db.execute(conn, "UPDATE local_prospects SET followups=followups+1, last_followup_at=%s, last_contacted_at=%s WHERE id=%s", (t, t, p["id"]))
    conn.commit()
    log.info("[mail] %s envoyé au prospect %s", kind, p["id"])
    if sent_copy:
        sent_copy(msg)
    return "sent"


def run(conn, now: dt.datetime | None = None, send=smtp_send, imap=inbox.connect, dns=maildns.doh_txt) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg, out = mail_config(), {}
    missing = missing_config(cfg)
    if not missing:
        try:
            out["inbox"] = inbox.poll(conn, cfg, factory=imap)
        except Exception as exc:     # noqa: BLE001 — une boîte illisible n'empêche pas d'abandonner les séquences, mais bloque les relances
            conn.rollback()
            _error(conn, f"lecture de la boîte impossible ({type(exc).__name__}) : {exc}")
            out["inbox_error"] = True
    out["abandoned"] = abandon(conn, now)
    auto = mailer.settings(_setting(conn, "local_auto_send"))
    checks = _state(conn)["checks"]
    checks["config_missing"] = missing
    if not auto["enabled"]:
        _save_checks(conn, checks)
        conn.commit()
        return {**out, "status": "disabled"}
    if missing:
        _save_checks(conn, checks)
        conn.commit()
        return {**out, "status": "config"}
    domain, prev = mailer.email_domain(cfg["from"]), checks.get("dns") or {}
    try:
        age = now - dt.datetime.fromisoformat(prev["at"])
    except (KeyError, TypeError, ValueError):
        age = None
    if prev.get("domain") != domain or prev.get("selector") != cfg["dkim_selector"] or age is None or not dt.timedelta(0) <= age < DNS_TTL:
        checks["dns"] = {**maildns.check(domain, cfg["dkim_selector"], txt=dns), "domain": domain, "selector": cfg["dkim_selector"], "at": now.isoformat()}
    _save_checks(conn, checks)
    conn.commit()
    if not checks["dns"]["ok"]:
        alerts.alert("local_mail_dns", "Envoi automatique en attente — DNS : " + " ; ".join(checks["dns"]["problems"]))
        return {**out, "status": "dns"}
    if out.get("inbox_error"):
        return {**out, "status": "inbox"}            # sans lecture de la boîte, on ne risque pas une relance à quelqu'un qui a répondu
    if not mailer.in_window(now, auto):
        return {**out, "status": "hours"}
    sent_today = db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_emails WHERE status IN ('sent','bounced') AND sent_at >= %s",
                              (mailer.paris_midnight_utc(now),))["n"]
    if sent_today >= auto["daily_cap"]:
        return {**out, "status": "cap"}
    sender = _setting(conn, "local_sender")
    for _ in range(5):                  # quelques prospects retirés (opposition, adresse perso…) ne gâchent pas le passage
        task = next_task(conn, now)
        if not task:
            return {**out, "status": "idle"}
        result = send_one(conn, task[0], task[1], cfg, sender, now, send=send,
                          sent_copy=lambda m: inbox.append_sent(cfg, m.as_bytes(), factory=imap), personal=auto["personal_emails"])
        if result != "skipped":
            return {**out, "status": result, "kind": task[0]}
    return {**out, "status": "idle"}


def main() -> int:
    with db.connect() as conn:
        result = run(conn)
    if result.get("status") not in ("disabled", "idle", "hours", "cap"):
        log.info("[mail] passage : %s", result)
    return 1 if result.get("status") == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
