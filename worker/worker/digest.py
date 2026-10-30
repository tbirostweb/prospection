"""Résumés Telegram de la prospection locale : quotidien (prospects du jour) et hebdo (entonnoir).

Lancé par cron :
  python -m worker.digest daily     # tous les jours à 07:15 : relances du jour, 3 meilleurs NOUVEAUX prospects (jamais deux fois), tournée
  python -m worker.digest weekly    # le lundi à 08:00

Aucune IA : uniquement des requêtes SQL agrégées.
"""
from __future__ import annotations

import sys

from . import db, notify
from .config import APP_URL, log
from .local import health, today


def _link(path: str, label: str) -> str:
    return f'<a href="{notify.esc(APP_URL)}{path}">{notify.esc(label)}</a>' if APP_URL else notify.esc(label)


def _name(r: dict) -> str:
    return (r.get("trade_name") or r.get("company_name") or "").strip().title()


def daily() -> None:
    """Le matin : 🔁 relances du jour, ⭐ les meilleurs NOUVEAUX prospects à contacter, 🗺️ la tournée proposée. Rien n'est envoyé aux prospects.

    Un prospect présenté dans « les meilleurs à contacter » n'y revient plus les jours suivants (digest_featured_at, marqué seulement si
    Telegram a accepté le message) : il reste dans l'application. Moins de 3 nouveaux = seulement ceux-là ; aucun = section remplacée par
    une ligne courte ; résumé entièrement vide (ni relance, ni nouveau prospect, ni tournée, ni problème) = aucun message.
    Les relances du jour ne sont PAS concernées : elles reviennent tant qu'elles sont dues."""
    with db.connect() as conn:
        due = today.followups(conn)
        remind = today.reminders(conn)
        top = today.top_prospects(conn, 3, fresh_only=True)
        tour = today.tour(conn, 6)
        todo = db.fetch_one(conn, f"SELECT COUNT(*) AS n FROM local_prospects p WHERE category IN {today.GOOD} AND status IN {today.OPEN_STATUSES} AND {today.CLEAN} AND {today.READY}")["n"]
        problems = health.degradations(conn)
        if not (remind or due or top or tour or problems):
            log.info("[digest] résumé du matin vide (aucune relance, aucun nouveau prospect, pas de tournée) : rien envoyé")
            return
        lines = ["☀️ <b>Ta journée de prospection</b>"]
        if remind:
            lines.append(f"\n⏰ <b>À rappeler ({len(remind)})</b>")
            for r in remind[:8]:
                how = f"☎ {r['phone']}" if r.get("phone") else ("✉ " + r["email"] if r.get("email") else "")
                tag = "🔥 intéressé · " if r.get("reply_kind") == "interested" else ""
                note = f" — {notify.esc(r['remind_note'])}" if r.get("remind_note") else ""
                page = _link(f"/local/{r['id']}", _name(r))
                lines.append(f"• {page}{note}\n   {tag}{notify.esc(how)}".rstrip())
            if len(remind) > 8:
                lines.append(f"   … et {len(remind) - 8} autre(s) : {_link('/local/envois', 'Envois')}")
        if due:
            lines.append(f"\n🔁 <b>Relances du jour ({len(due)})</b>")
            for r in due[:6]:
                how = f"☎ appeler · {today.best_time(r['activity_key'])}" if r.get("phone") else ("✉ par e-mail" if r.get("email") else "")
                page = _link(f"/local/{r['id']}", _name(r))
                lines.append(f"• {page} — {notify.esc(r['due'])}" + (f"\n   {notify.esc(how)}" if how else ""))
            if len(due) > 6:
                lines.append(f"   … et {len(due) - 6} autre(s) : {_link('/local/contact', 'À contacter')}")
        if top:
            lines.append("\n⭐ <b>" + (f"Les {len(top)} meilleurs nouveaux à contacter" if len(top) > 1 else "Le meilleur nouveau à contacter") + "</b>")
            for r in top:
                how = f"☎ appeler · {today.best_time(r['activity_key'])}" if r.get("phone") else ("✉ e-mail" if r.get("email") else "passer sur place")
                page, city = _link(f"/local/{r['id']}", _name(r)), notify.esc((r["city"] or "").title())
                lines.append(f"• {page} ({city}) — {r['prospect_score']}/100"
                             + (f"\n   {'⚡ ' if r.get('trigger') else ''}{notify.esc(r['signal'])}" if r.get("signal") else "") + f"\n   {notify.esc(how)}")
        elif todo:
            lines.append(f"\n⭐ Aucun nouveau prospect depuis le dernier résumé : ceux déjà présentés restent dans {_link('/local/contact', 'À contacter')}.")
        if tour:
            stops = " → ".join(notify.esc(_name(p)) for p in tour["stops"])
            lines.append(f"\n🗺️ <b>Tournée proposée</b> ({len(tour['stops'])} arrêts, ≈ {tour['km']} km)\n{stops}")
            maps = notify.esc(tour["maps_url"])
            details = _link(f"/local/tournee?c={tour['campaign_id']}", "détails")
            lines.append(f'<a href="{maps}">Ouvrir l\'itinéraire</a>' + (f" · {details}" if APP_URL else ""))
        if not (remind or due or top):
            lines.append("\nRien d'urgent : aucune relance due et aucun nouveau prospect « À contacter ».")
        lines.append(f"\n{todo} fiche(s) prête(s) à contacter au total")
        for p in problems:
            lines.append(f"⚠ {notify.esc(p['message'])}")
        sent = notify.send_text("\n".join(lines))
        if sent:
            today.mark_featured(conn, [r["id"] for r in top])     # Telegram en panne = rien de marqué : ils seront proposés demain
    log.info("[digest] résumé du matin %s (%d relance(s), %d nouveau(x) prospect(s), tournée %s)",
             "envoyé" if sent else "NON envoyé", len(due), len(top), "oui" if tour else "non")


def weekly() -> None:
    with db.connect() as conn:
        funnel = health.funnel(conn)
        errs = health.error_stats(conn, 7)
        engines = health.engines_status(conn)
    lines = ["📊 <b>Bilan de la semaine</b>", "<b>Entonnoir</b>"]
    lines += [f"• {notify.esc(s['label'])} : {s['count']}" for s in funnel]
    if errs:
        lines.append("\n<b>Erreurs (7 j)</b>")
        lines += [f"• {notify.esc(e['category'])} : {e['n']}" for e in errs[:6]]
    if engines:
        lines.append("\n<b>Moteurs de recherche</b>")
        lines += [f"• {notify.esc(e['engine'])} : {e['state']} (santé {e['health'] if e['health'] is not None else '—'})" for e in engines]
    notify.send_text("\n".join(lines))
    log.info("[digest] bilan hebdomadaire envoyé")


if __name__ == "__main__":
    kind = sys.argv[1] if len(sys.argv) > 1 else "daily"
    {"daily": daily, "weekly": weekly}.get(kind, daily)()
