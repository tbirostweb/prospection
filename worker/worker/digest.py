"""Résumés Telegram de la prospection locale : quotidien (prospects du jour) et hebdo (entonnoir).

Lancé par cron :
  python -m worker.digest daily     # tous les jours à 07:15 : relances du jour, 3 meilleurs prospects, tournée
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


def _phone(r: dict) -> str:
    return " ".join(r["phone"][i:i + 2] for i in range(0, len(r["phone"]), 2)) if r.get("phone") else ""


def daily() -> None:
    """Le matin : 🔁 relances du jour, ⭐ les 3 meilleurs prospects à contacter, 🗺️ la tournée proposée. Rien n'est envoyé aux prospects."""
    with db.connect() as conn:
        due = today.followups(conn)
        top = today.top_prospects(conn, 3)
        tour = today.tour(conn, 6)
        todo = db.fetch_one(conn, f"SELECT COUNT(*) AS n FROM local_prospects p WHERE category IN {today.GOOD} AND status IN {today.OPEN_STATUSES} AND {today.CLEAN} AND {today.READY}")["n"]
        problems = health.degradations(conn)
    lines = ["☀️ <b>Ta journée de prospection</b>"]
    if due:
        lines.append(f"\n🔁 <b>Relances du jour ({len(due)})</b>")
        for r in due[:6]:
            how = f"☎ {_phone(r)} · {today.best_time(r['activity_key'])}" if r.get("phone") else ("✉ par e-mail" if r.get("email") else "")
            page = _link(f"/local/{r['id']}", _name(r))
            lines.append(f"• {page} — {notify.esc(r['due'])}" + (f"\n   {notify.esc(how)}" if how else ""))
        if len(due) > 6:
            lines.append(f"   … et {len(due) - 6} autre(s) : {_link('/local/contact', 'À contacter')}")
    if top:
        lines.append("\n⭐ <b>Les 3 meilleurs à contacter</b>")
        for r in top:
            how = f"☎ {_phone(r)} · {today.best_time(r['activity_key'])}" if r.get("phone") else ("✉ e-mail" if r.get("email") else "passer sur place")
            page, city = _link(f"/local/{r['id']}", _name(r)), notify.esc((r["city"] or "").title())
            lines.append(f"• {page} ({city}) — {r['prospect_score']}/100"
                         + (f"\n   {notify.esc(r['signal'])}" if r.get("signal") else "") + f"\n   {notify.esc(how)}")
    if tour:
        stops = " → ".join(notify.esc(_name(p)) for p in tour["stops"])
        lines.append(f"\n🗺️ <b>Tournée proposée</b> ({len(tour['stops'])} arrêts, ≈ {tour['km']} km)\n{stops}")
        maps = notify.esc(tour["maps_url"])
        details = _link(f"/local/tournee?c={tour['campaign_id']}", "détails")
        lines.append(f'<a href="{maps}">Ouvrir l\'itinéraire</a>' + (f" · {details}" if APP_URL else ""))
    if not (due or top):
        lines.append("\nRien d'urgent : aucune relance due et aucun prospect « À contacter » en attente.")
    lines.append(f"\n{todo} fiche(s) prête(s) à contacter au total")
    for p in problems:
        lines.append(f"⚠ {notify.esc(p['message'])}")
    notify.send_text("\n".join(lines))
    log.info("[digest] résumé du matin envoyé (%d relance(s), %d prospect(s), tournée %s)", len(due), len(top), "oui" if tour else "non")


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
