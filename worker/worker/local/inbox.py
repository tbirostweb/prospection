"""Lecture de la boîte de réception (IMAP, LECTURE SEULE) pour arrêter les relances dès qu'un prospect répond.

Aucun message n'est modifié, déplacé ni marqué lu (EXAMINE + BODY.PEEK). Seuls les messages arrivés depuis le dernier passage sont lus.
Une réponse est reconnue par ses en-têtes In-Reply-To / References (Message-ID de nos e-mails), sinon par l'adresse ou le domaine de
l'expéditeur. Selon le contenu : refus explicite -> liste « ne plus contacter » ; « pas intéressé » -> perdu ; « plus tard » -> a répondu + rappel
dans 3 mois ; « intéressé » -> a répondu + à rappeler aujourd'hui ; sinon -> a répondu, sans catégorie (alerte Telegram). Les règles sont des
mots-clés prudents : au moindre doute (négation, message long, ton ambigu) c'est « a répondu » neutre. La catégorie et un extrait sont gardés sur la fiche. Les retours d'erreur définitifs (adresse inexistante) marquent l'adresse comme invalide. Les réponses automatiques sont ignorées.
"""
from __future__ import annotations

import datetime as dt
import email
import html as htmllib
import imaplib
import re
import time
import unicodedata
from email.message import Message
from email.policy import default as default_policy
from email.utils import getaddresses, parseaddr

from .. import db, notify
from ..config import APP_URL, log
from .contacts import FREEMAIL
from .mailer import PARIS, email_domain

MAX_PER_RUN = 200
WATCH_DAYS = 45                       # on ne lit la boîte que s'il y a eu un envoi automatique récent
MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

OPT_OUT = re.compile(r"ne (plus|pas) (me |nous )?(contacter|recontacter|ecrire|solliciter|relancer)|plus (me|nous) (contacter|ecrire|solliciter)|"
                     r"desinscri|desabonn|unsubscribe|\bstop\b|retire[rz]? (mon|notre|nos|mes|cette)|supprime[rz]? (mon|notre|nos|mes|cette)|"
                     r"pas de (demarchage|prospection|sollicitation)|\bopposition\b|bloctel|harcel")
NOT_INTERESTED = re.compile(r"pas interess|non merci|pas besoin|pas pour (l'instant|nous)|ne (nous|m') interesse pas|"
                            r"deja (un|une|notre|mon) (site|prestataire|agence|webmaster)")
LATER = re.compile(r"plus tard|pas pour le moment|pas le moment|pas maintenant|dans quelques (mois|semaines)|(l'|une )?annee prochaine|"
                   r"revenez (vers|me voir|nous voir)|reprendre contact (dans|en|plus|apres)|recontactez[- ](moi|nous) (dans|en|plus|apres)|"
                   r"apres (l'ete|les fetes|la saison|les vacances)")
INTERESTED = re.compile(r"\boui\b|interess|rappelez[- ]?(moi|nous)|appelez[- ]?(moi|nous)|(me|nous) (rappeler|appeler|contacter par telephone)|\bdevis\b|"
                        r"rendez[- ]?vous|\brdv\b|disponible|quand (etes|pouvez|seriez|pourriez|passez|venez)")
NEGATION = re.compile(r"\b(pas|non|jamais|aucun|aucune|sans|impossible)\b|n'(est|ai|avons|a)\b")
INTERESTED_MAX_CHARS = 500            # un long message est nuancé : on ne devine pas
EXCERPT_CHARS = 400
QUOTE_START = re.compile(r"^(le .{5,120} a ecrit ?:|on .{5,120} wrote:|-{2,} ?(original|message d'origine|forwarded)|de ?: |from: |envoye ?: )", re.I)
AUTO_SUBJECT = re.compile(r"absen|out of office|r[ée]ponse automatique|automatic reply|auto.?reply|accus[ée] de r[ée]ception|cong[ée]s", re.I)


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().replace("’", "'")


def reply_text(msg: Message) -> str:
    """Ce que l'expéditeur a écrit, sans le message cité ni sa signature lointaine (2 000 caractères au plus)."""
    part = msg.get_body(preferencelist=("plain", "html")) if hasattr(msg, "get_body") else None
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, ValueError):
        return ""
    if part.get_content_subtype() == "html":
        text = re.sub(r"(?is)<(script|style|blockquote).*?</\1>", " ", text)
        text = htmllib.unescape(re.sub(r"(?s)<br\s*/?>|</p>|</div>", "\n", text))
        text = re.sub(r"(?s)<[^>]+>", " ", text)
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(">") or QUOTE_START.match(fold(s)):
            break
        out.append(s)
    return "\n".join(out).strip()[:2000]


def classify(msg: Message) -> str:
    """'bounce' (erreur définitive) · 'delay' · 'auto' (réponse automatique) · 'message'."""
    sender = parseaddr(str(msg.get("From", "")))[1].lower()
    ctype = msg.get_content_type()
    if ctype == "multipart/report" or re.match(r"(mailer-daemon|postmaster)@", sender):
        raw = msg.as_string()
        if re.search(r"(?im)^action:\s*(delayed|delivered|relayed|expanded)", raw) and not re.search(r"(?im)^action:\s*failed", raw):
            return "delay"
        return "bounce" if re.search(r"(?im)^(action:\s*failed|status:\s*5\.)|\b5\.\d\.\d+\b|\b55\d\b", raw) else "delay"
    if (str(msg.get("Auto-Submitted", "no")).lower() not in ("", "no") or msg.get("X-Autoreply") or msg.get("X-Autorespond")
            or str(msg.get("Precedence", "")).lower() in ("auto_reply", "bulk", "junk") or AUTO_SUBJECT.search(str(msg.get("Subject", "")))):
        return "auto"
    return "message"


def outcome(text: str, subject: str = "") -> str:
    """'opted_out' (ne plus contacter) · 'not_interested' · 'later' (plus tard) · 'interested' · 'replied' (neutre, au moindre doute)."""
    t = fold(f"{subject}\n{text}")
    if OPT_OUT.search(t) or fold(subject).strip() in ("desinscription", "stop"):
        return "opted_out"
    if NOT_INTERESTED.search(t):
        return "not_interested"
    if LATER.search(t):
        return "later"
    body = fold(text)                  # l'objet (« Re: Votre site… ») ne compte pas pour juger l'intérêt
    if len(body) <= INTERESTED_MAX_CHARS and INTERESTED.search(body) and not NEGATION.search(body.replace("pourquoi pas", "")):
        return "interested"
    return "replied"


def add_months(day: dt.date, months: int) -> dt.date:
    """Même jour `months` mois plus tard (le 31 devient le dernier jour du mois d'arrivée)."""
    y, m = divmod(day.month - 1 + months, 12)
    y, m = day.year + y, m + 1
    last = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)).day
    return dt.date(y, m, min(day.day, last))


def paris_today(now: dt.datetime | None = None) -> dt.date:
    """Le jour en cours à Paris (les rappels sont des jours calendaires)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(PARIS).date()


def excerpt(text: str) -> str | None:
    return re.sub(r"\s+", " ", text).strip()[:EXCERPT_CHARS] or None


def _refs(msg: Message) -> list[str]:
    return re.findall(r"<[^<>\s]+>", f"{msg.get('In-Reply-To', '')} {msg.get('References', '')}")


def match(conn, msg: Message, kind: str) -> dict | None:
    """Notre e-mail (et son prospect) auquel ce message répond, ou None (message sans rapport : ignoré)."""
    if kind == "bounce":
        raw = msg.as_string()
        ids = re.findall(r"<[^<>\s]+@[^<>\s]+>", raw)
        for mid in ids:
            row = db.fetch_one(conn, "SELECT * FROM local_emails WHERE message_id=%s", (mid,))
            if row:
                return row
        for addr in re.findall(r"(?im)^(?:final|original)-recipient:\s*rfc822;\s*<?([^\s<>]+@[^\s<>]+)>?", raw):
            row = db.fetch_one(conn, "SELECT * FROM local_emails WHERE to_email=%s ORDER BY sent_at DESC LIMIT 1", (addr.lower(),))
            if row:
                return row
        return None
    for mid in _refs(msg):
        row = db.fetch_one(conn, "SELECT * FROM local_emails WHERE message_id=%s", (mid,))
        if row:
            return row
    senders = [a.lower() for _n, a in getaddresses([str(msg.get("From", "")), str(msg.get("Reply-To", ""))]) if "@" in a]
    for addr in senders:
        row = db.fetch_one(conn, "SELECT * FROM local_emails WHERE to_email=%s ORDER BY sent_at DESC LIMIT 1", (addr,))
        if row:
            return row
    for addr in senders:                  # un collègue répond depuis une autre adresse du même domaine (jamais pour une messagerie gratuite)
        dom = email_domain(addr)
        if dom and dom not in FREEMAIL:
            row = db.fetch_one(conn, "SELECT * FROM local_emails WHERE to_email LIKE %s ORDER BY sent_at DESC LIMIT 1", (f"%@{dom}",))
            if row:
                return row
    return None


def _name(p: dict) -> str:
    return htmllib.escape(re.sub(r"\s+", " ", p.get("trade_name") or p.get("company_name") or "Un prospect"))


def _link(p: dict) -> str:
    return f'\n<a href="{APP_URL}/local/{p["id"]}">Ouvrir la fiche</a>' if APP_URL else ""


def _alert_reply(p: dict, label: str, body: str) -> str:
    """Alerte immédiate (pastille violette) : « 🟣 Réponse intéressée : entreprise (ville) — « extrait » — lien »."""
    ville = f" ({htmllib.escape(p['city'])})" if p.get("city") else ""
    ext = htmllib.escape((excerpt(body) or "")[:200])
    lien = f' — <a href="{APP_URL}/local/{p["id"]}">Ouvrir la fiche</a>' if APP_URL else ""
    return f"🟣 {label} : <b>{_name(p)}</b>{ville} — « {ext} »{lien}"


def apply(conn, sent: dict, kind: str, msg: Message) -> str | None:
    """Met à jour le prospect selon le message reçu ; renvoie ce qui a été fait (pour le journal)."""
    p = db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (sent["prospect_id"],))
    if not p:
        return None
    if kind == "bounce":
        db.execute(conn, "UPDATE local_emails SET status='bounced' WHERE id=%s", (sent["id"],))
        db.execute(conn, "UPDATE local_prospects SET auto_state='bounced' WHERE id=%s", (p["id"],))
        notify.send_text(f"⚠️ <b>{_name(p)}</b> : l'e-mail est revenu (adresse invalide). Plus d'envoi automatique : à contacter par téléphone ou sur place.{_link(p)}")
        return "bounced"
    body = reply_text(msg)
    result = outcome(body, str(msg.get("Subject", "")))
    if result == "opted_out":
        db.execute(conn, "INSERT INTO local_do_not_contact (siret, siren, domain, email, reason) VALUES (%s,%s,%s,%s,%s)",
                   (p["siret"], p["siren"], p.get("canonical_domain"), sent["to_email"], "a demandé à ne plus être contacté (réponse à un e-mail)"))
        db.execute(conn, """UPDATE local_prospects SET do_not_contact=1, status='DO_NOT_CONTACT', auto_state='opted_out', response_status='NOT_INTERESTED',
                            last_prospect_contact_at=UTC_TIMESTAMP() WHERE id=%s""", (p["id"],))
        notify.send_text(f"🚫 <b>{_name(p)}</b> a demandé à ne plus être contacté : ajouté à la liste « ne plus contacter », plus aucun envoi.{_link(p)}")
        return result
    # Une réponse n'écrase jamais un statut posé à la main (intéressé, gagné…).
    worked = p["status"] in ("CONTACTED", "LOST", "TO_CONTACT", "QUALIFIED")
    # Aucune relance automatique après une réponse : auto_state='replied' (l'envoi ne relance que auto_state='active').
    sets = ["auto_state='replied'", "last_prospect_contact_at=UTC_TIMESTAMP()", "reply_kind=%s", "reply_excerpt=%s", "reply_at=UTC_TIMESTAMP()"]
    args: list = [result, excerpt(body)]
    stage = "LOST" if result == "not_interested" and worked else "REPLIED"
    if not p.get("deal_stage") or stage == "LOST":
        sets += ["deal_stage=%s", "deal_stage_at=UTC_TIMESTAMP()"]
        args.append(stage)
    today = paris_today()
    if result == "not_interested":
        if worked:
            sets.append("status='LOST', response_status='NOT_INTERESTED'")
        msg = f"💬 <b>{_name(p)}</b> a répondu : pas intéressé pour l'instant. Relances arrêtées.{_link(p)}"
    elif result in ("interested", "later"):
        if worked:
            sets.append("status='INTERESTED', response_status='INTERESTED'" if result == "interested" else "status='REPLIED', response_status='REPLIED'")
        when, note = (today, "A répondu : intéressé, à rappeler") if result == "interested" else (add_months(today, 3), "A répondu : plus tard, à recontacter")
        current = p.get("remind_at")
        if current is None or (current > when if result == "interested" else current < when):    # intéressé : le plus tôt gagne ; plus tard : remplace un rappel plus proche, jamais un rappel plus lointain
            sets += ["remind_at=%s", "remind_note=%s"]
            args += [when, note]
        msg = (_alert_reply(p, "Réponse intéressée", body) if result == "interested"
               else f"🕒 <b>{_name(p)}</b> a répondu : plus tard. Rappel posé le {when.strftime('%d/%m/%Y')}. Relances arrêtées.{_link(p)}")
    else:
        if worked:
            sets.append("status='REPLIED', response_status='REPLIED'")
        msg = _alert_reply(p, "Nouvelle réponse", body)
    db.execute(conn, f"UPDATE local_prospects SET {', '.join(sets)} WHERE id=%s", (*args, p["id"]))
    try:
        notify.send_text(msg)    # une alerte qui échoue ne doit jamais faire échouer la lecture de la boîte
    except Exception as e:
        log.warning("[inbox] alerte de réponse non envoyée : %s", e)
    return result


def _since(day: dt.date) -> str:
    return f"{day.day:02d}-{MONTHS_EN[day.month - 1]}-{day.year}"


def connect(cfg: dict) -> imaplib.IMAP4:
    box = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"], timeout=30)
    box.login(cfg["user"], cfg["password"])
    return box


def poll(conn, cfg: dict, factory=connect) -> dict:
    """Lit les nouveaux messages de la boîte de réception. Renvoie {'read': n, 'replied': n, ...}."""
    stats: dict = {"read": 0}
    first = db.fetch_one(conn, f"SELECT MIN(sent_at) AS d FROM local_emails WHERE sent_at > UTC_TIMESTAMP() - INTERVAL {WATCH_DAYS} DAY")
    if not first or not first["d"]:
        return stats
    state = db.fetch_one(conn, "SELECT * FROM local_mail_state WHERE id=1") or {}
    box = factory(cfg)
    try:
        typ, _ = box.select("INBOX", readonly=True)
        if typ != "OK":
            raise RuntimeError("boîte de réception illisible")
        validity = int((box.response("UIDVALIDITY")[1] or [b"0"])[0] or 0)
        last = state.get("imap_last_uid") if state.get("imap_validity") == validity else None
        if last:
            typ, data = box.uid("search", None, f"UID {int(last) + 1}:*")
        else:
            typ, data = box.uid("search", None, f"SINCE {_since(first['d'].date() - dt.timedelta(days=1))}")
        uids = sorted(int(u) for u in (data[0] or b"").split() if not last or int(u) > int(last))[:MAX_PER_RUN]
        top = int(last or 0)
        for uid in uids:
            typ, parts = box.uid("fetch", str(uid), "(BODY.PEEK[]<0.65536>)")
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
            top = max(top, uid)
            if typ != "OK" or not raw:
                continue
            msg = email.message_from_bytes(raw, policy=default_policy)
            stats["read"] += 1
            kind = classify(msg)
            if kind in ("auto", "delay"):
                continue
            sent = match(conn, msg, kind)
            if sent:
                done = apply(conn, sent, kind, msg)
                if done:
                    stats[done] = stats.get(done, 0) + 1
        db.execute(conn, """INSERT INTO local_mail_state (id, imap_validity, imap_last_uid, imap_at) VALUES (1, %s, %s, UTC_TIMESTAMP())
                            ON DUPLICATE KEY UPDATE imap_validity=VALUES(imap_validity), imap_last_uid=VALUES(imap_last_uid), imap_at=VALUES(imap_at)""",
                   (validity, top or None))
        conn.commit()
    finally:
        try:
            box.logout()
        except Exception:     # noqa: BLE001 — fermeture best-effort
            pass
    if len(stats) > 1:
        log.info("[mail] boîte lue : %s", stats)
    return stats


def append_sent(cfg: dict, raw: bytes, factory=connect) -> bool:
    """Copie l'e-mail envoyé dans le dossier « Envoyés » de la boîte (SMTP ne le fait pas) : la conversation reste complète chez toi."""
    try:
        box = factory(cfg)
    except Exception as exc:     # noqa: BLE001
        log.warning("[mail] copie dans Envoyés impossible : %s", exc)
        return False
    try:
        typ, folders = box.list()
        name = None
        for f in folders or []:
            line = f.decode(errors="replace") if isinstance(f, bytes) else str(f)
            if "\\Sent" in line:
                name = line.rsplit(' "', 1)[-1].rstrip('"') if line.endswith('"') else line.rsplit(" ", 1)[-1]
                break
        if not name:
            return False
        typ, _ = box.append(f'"{name}"' if " " in name else name, "(\\Seen)", imaplib.Time2Internaldate(time.time()), raw)
        return typ == "OK"
    except Exception as exc:     # noqa: BLE001
        log.warning("[mail] copie dans Envoyés impossible : %s", exc)
        return False
    finally:
        try:
            box.logout()
        except Exception:     # noqa: BLE001
            pass
