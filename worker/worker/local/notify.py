"""Alertes Telegram des prospects locaux qualifiés (« À contacter » ou « Très bon »), avec boutons de classement. Aucun contact automatique."""
from __future__ import annotations

import json

from .. import db, notify
from ..config import APP_URL, log

MAX_PER_RUN = 8


def _j(v):
    return json.loads(v) if isinstance(v, (str, bytes)) else v


def notify_qualified(conn, limit: int = MAX_PER_RUN, send=None) -> dict:
    """Envoie les prospects FINAUX « Très bon » / « À contacter » pas encore notifiés (les meilleurs d'abord, `limit` par passage, puis UN récapitulatif).
    Un prospect n'est marqué notifié que si Telegram a ACCEPTÉ le message : Telegram non configuré ou en panne = rien n'est perdu, tout partira plus tard."""
    send = send or notify.send_prospect
    rows = db.fetch_all(conn, """SELECT * FROM local_prospects WHERE score_stage='FINAL' AND category IN ('TRES_BON','A_CONTACTER') AND notified_at IS NULL
                                 AND do_not_contact=0 AND excluded_reason IS NULL AND status IN ('DISCOVERED','ENRICHED','AUDITED','QUALIFIED')
                                 ORDER BY prospect_score DESC, id LIMIT %s""", (limit + 50,))
    sent = failed = 0
    for r in rows[:limit]:
        sd = _j(r.get("score_details")) or {}
        if send(r, _j(r.get("issues")) or [], sd.get("summary") or []):
            db.execute(conn, "UPDATE local_prospects SET notified_at=UTC_TIMESTAMP() WHERE id=%s", (r["id"],))
            conn.commit()
            sent += 1
        else:
            failed += 1
            break                                   # Telegram indisponible : inutile d'insister à chaque prospect
    rest = len(rows) - sent - failed
    if sent and rest > 0:
        notify.send_text(f"📋 {rest} autre(s) prospect(s) « À contacter » t'attendent dans l'application (Prospection locale → À contacter).")
    if sent:
        log.info("[local] %d alerte(s) Telegram envoyée(s), %d en attente", sent, max(0, rest))
    return {"sent": sent, "failed": failed, "waiting": max(0, rest)}


def notify_new_businesses(conn, send_text=None) -> dict:
    """Veille : UN message par campagne listant les nouvelles entreprises, une fois évaluées (ou après 12 h), jamais deux fois.
    Chaînes, exclus et « ne plus contacter » ne sont jamais annoncés."""
    send_text = send_text or notify.send_text
    rows = db.fetch_all(conn, """SELECT p.*, c.id AS campaign_id, c.name AS campaign_name FROM local_prospects p
                                 JOIN local_prospect_campaigns lc ON lc.prospect_id = p.id JOIN local_campaigns c ON c.id = lc.campaign_id
                                 WHERE p.new_business_at IS NOT NULL AND p.new_business_notified_at IS NULL
                                   AND (p.score_stage = 'FINAL' OR p.new_business_at < UTC_TIMESTAMP() - INTERVAL 12 HOUR)
                                 ORDER BY c.id, p.prospect_score DESC""")
    by: dict[int, list[dict]] = {}
    for r in rows:
        by.setdefault(r["campaign_id"], []).append(r)
    sent = 0
    for _cid, group in by.items():
        ids = list(dict.fromkeys(r["id"] for r in group))
        good = [r for r in group if not r["do_not_contact"] and not r["excluded_reason"] and not r["is_chain"]]
        good = list({r["id"]: r for r in good}.values())
        if good:
            lines = [f"🆕 <b>Veille — {len(good)} nouvelle(s) entreprise(s)</b> · {notify.esc(group[0]['campaign_name'])}"]
            for r in good[:12]:
                site = notify.SITE_LABEL.get(r.get("website_status") or "", "site pas encore recherché")
                created = r["company_created_at"].strftime("%d/%m/%Y") if hasattr(r.get("company_created_at"), "strftime") else "date inconnue"
                contact = " · ☎" if r.get("phone") else ""
                contact += " · ✉" if r.get("email") else ""
                link = f" · {notify.esc(APP_URL)}/local/{r['id']}" if APP_URL else ""
                score = f" · {int(r['prospect_score'])}/100" if r.get("score_stage") == "FINAL" and r.get("prospect_score") is not None else ""
                name = notify.esc((r["trade_name"] or r["company_name"]).title())
                lines.append(f"• <b>{name}</b> — {notify.esc(r.get('activity_label') or 'activité ?')}, {notify.esc((r.get('city') or '').title())}"
                             f" · créée le {created} · {notify.esc(site)}{score}{contact}{link}")
            if len(good) > 12:
                lines.append(f"… et {len(good) - 12} autre(s) dans l'application (vue « Nouvelles entreprises »).")
            lines.append("<i>Une création est un signal, pas une demande : à toi de juger.</i>")
            if not send_text("\n".join(lines)):
                break                                   # Telegram indisponible : rien n'est marqué, tout repartira plus tard
            sent += len(good)
        db.execute(conn, "UPDATE local_prospects SET new_business_notified_at=UTC_TIMESTAMP() WHERE id IN (%s)" % ",".join(["%s"] * len(ids)), tuple(ids))
        conn.commit()
    return {"sent": sent}
