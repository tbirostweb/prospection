"""Rapport d'auto-audit du système, LECTURE SEULE :  python -m worker.audit_system [--json]

Sources · moteurs de recherche indisponibles · campagnes bloquées · prospects en erreur · « site non trouvé » anormalement nombreux ·
taux de contact faible · audits échoués · données anciennes. Ne modifie RIEN (aucun INSERT / UPDATE / DELETE).
Code de sortie 1 s'il y a au moins un problème CRITIQUE.
"""
from __future__ import annotations

import json
import sys

from . import db
from .local import health


def collect(conn) -> list[dict]:
    """[{"section", "level": ok|warning|critical, "message"}] — jamais d'exception : une section illisible est elle-même signalée."""
    out: list[dict] = []

    def add(section: str, level: str, msg: str) -> None:
        out.append({"section": section, "level": level, "message": msg})

    def safe(section, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — un audit qui plante ne dirait rien : on le signale
            add(section, "warning", f"section illisible : {exc.__class__.__name__}: {exc}")

    def sources():
        for s in health.source_status(conn):
            if s["errors_recent"]:
                add("Sources", "warning", f"{s['label']} : {s['errors_recent']} erreur(s) sur 7 jours (dernière {s['last_error_at']})")
        add("Sources", "ok", "SIRENE, BODACC, recherche du site, OSM : voir les erreurs ci-dessus le cas échéant")

    def engines():
        rows = health.engines_status(conn)
        if not rows:
            add("Moteurs de recherche", "warning", "aucun moteur encore utilisé par la prospection locale (aucune requête passée)")
            return
        down = [r for r in rows if r["state"] in ("cooldown", "désactivé")]
        for r in rows:
            if r["state"] != "ok":
                add("Moteurs de recherche", "warning", f"{r['engine']} : {r['state']} (santé {r['health']}, dernier échec : {r['last_error'] or '—'}, cooldown jusqu'à {r['cooldown_until']})")
        if len(down) == len(rows):
            add("Moteurs de recherche", "critical", "TOUS les moteurs sont indisponibles")
        elif not down:
            add("Moteurs de recherche", "ok", f"{len(rows)} moteur(s) disponible(s) : " + ", ".join(f"{r['engine']} {r['health']}" for r in rows))

    def campaigns():
        stuck = db.fetch_all(conn, """SELECT name, status, last_run_at, last_error FROM local_campaigns WHERE enabled=1 AND
                                      ((status='running' AND (last_run_at IS NULL OR last_run_at < UTC_TIMESTAMP() - INTERVAL 6 HOUR))
                                       OR (status='queued' AND created_at < UTC_TIMESTAMP() - INTERVAL 2 HOUR))""")
        for c in stuck:
            add("Campagnes", "warning", f"« {c['name']} » {c['status']} sans progrès (dernier passage {c['last_run_at']}) {c['last_error'] or ''}"[:200])
        waiting = db.fetch_all(conn, "SELECT name, last_error FROM local_campaigns WHERE status='running' AND last_error IS NOT NULL")
        for c in waiting:
            add("Campagnes", "warning", f"« {c['name']} » en attente : {c['last_error']}"[:200])
        errs = db.fetch_all(conn, "SELECT name, last_error FROM local_campaigns WHERE status='error'")
        for c in errs:
            add("Campagnes", "critical", f"« {c['name']} » en erreur : {c['last_error']}"[:200])
        for c in db.fetch_all(conn, "SELECT name, coverage FROM local_campaigns WHERE coverage IS NOT NULL"):
            cov = json.loads(c["coverage"]) if isinstance(c["coverage"], (str, bytes)) else c["coverage"]
            if cov and cov.get("partial"):
                add("Campagnes", "warning", f"« {c['name']} » PARTIELLE : {cov.get('imported')} importés sur {cov.get('units_available')} unités légales disponibles (arrêt : {cov.get('stopped')})")
        if not (stuck or waiting or errs):
            add("Campagnes", "ok", "aucune campagne bloquée")

    def prospects():
        r = db.fetch_all(conn, """SELECT error_category, COUNT(*) AS n, SUM(next_retry_at IS NULL) AS abandoned FROM local_prospects WHERE pipeline_stage='ERROR' GROUP BY error_category""")
        for x in r:
            add("Prospects en erreur", "warning" if int(x["abandoned"] or 0) == 0 else "critical", f"{x['n']} en {x['error_category']} dont {int(x['abandoned'] or 0)} abandonné(s)")
        if not r:
            add("Prospects en erreur", "ok", "aucun prospect en erreur")

    def site_rates():
        m = db.fetch_one(conn, """SELECT SUM(website_status IS NOT NULL) AS searched, SUM(website_status='NOT_FOUND') AS nf,
                                         SUM(website_status IN ('CONFIRMED','PROBABLE','UNREACHABLE')) AS ident, SUM(website_status='UNCERTAIN') AS unc,
                                         SUM(website_status IN ('CONFIRMED','PROBABLE','UNREACHABLE') AND (phone IS NOT NULL OR email IS NOT NULL OR contact_form=1)) AS with_contact,
                                         SUM(audited_at IS NOT NULL) AS audited
                                  FROM local_prospects WHERE excluded_reason IS NULL AND is_chain=0""")
        searched, nf, ident = int(m["searched"] or 0), int(m["nf"] or 0), int(m["ident"] or 0)
        if searched >= 20:
            level = "warning" if nf / searched > 0.8 else "ok"
            add("Sites non trouvés", level, f"{nf}/{searched} « non trouvé » ({nf / searched:.0%}), {int(m['unc'] or 0)} incertains, {ident} identifiés"
                + (" — anormalement élevé : vérifier les moteurs et la couverture des stratégies" if level == "warning" else ""))
        else:
            add("Sites non trouvés", "ok", f"échantillon trop petit ({searched} recherchés) pour juger")
        if ident >= 10:
            rate = int(m["with_contact"] or 0) / ident
            add("Contacts", "warning" if rate < 0.3 else "ok", f"contact professionnel trouvé pour {rate:.0%} des sites identifiés ({int(m['with_contact'] or 0)}/{ident})")
        if ident:
            fails = db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_events WHERE category IN ('AUDIT_PARSE_ERROR','SITE_TIMEOUT','SITE_HTTP_ERROR') AND at >= UTC_TIMESTAMP() - INTERVAL 7 DAY")["n"]
            add("Audits", "warning" if fails > max(3, ident // 4) else "ok", f"{int(m['audited'] or 0)} audits réalisés, {fails} échec(s) de lecture de site sur 7 jours")

    def old_data():
        r = db.fetch_one(conn, """SELECT SUM(website_status IN ('NOT_FOUND','UNCERTAIN') AND website_checked_at < UTC_TIMESTAMP() - INTERVAL 30 DAY) AS stale_site,
                                         SUM(website_status IN ('CONFIRMED','PROBABLE') AND audited_at < UTC_TIMESTAMP() - INTERVAL 30 DAY) AS stale_audit
                                  FROM local_prospects""")
        n = int(r["stale_site"] or 0) + int(r["stale_audit"] or 0)
        add("Données anciennes", "warning" if n else "ok", f"{n} donnée(s) à rafraîchir (sites non trouvés > 30 j : {int(r['stale_site'] or 0)}, audits > 30 j : {int(r['stale_audit'] or 0)}) — repris automatiquement par le worker" if n else "aucune donnée périmée")

    for name, fn in (("Sources", sources), ("Moteurs", engines), ("Campagnes", campaigns), ("Prospects", prospects), ("Sites", site_rates), ("Données", old_data)):
        safe(name, fn)
    for d in health.degradations(conn):
        add("Dégradation", d["level"], d["message"])
    return out


def main() -> int:
    as_json = "--json" in sys.argv
    try:
        with db.connect() as conn:
            conn.autocommit = True                                  # lecture seule : aucune transaction ouverte
            rows = collect(conn)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ base de données injoignable : {exc}")
        return 1
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, default=str, indent=1))
    else:
        icon = {"ok": "✅", "warning": "⚠️ ", "critical": "❌"}
        print("Audit du système (lecture seule — rien n'est modifié)\n")
        for r in rows:
            print(f"{icon[r['level']]} {r['section']} : {r['message']}")
        print(f"\n{sum(r['level'] == 'critical' for r in rows)} critique(s), {sum(r['level'] == 'warning' for r in rows)} avertissement(s).")
    return 1 if any(r["level"] == "critical" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
