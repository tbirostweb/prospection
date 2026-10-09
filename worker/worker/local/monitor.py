"""Surveillance des dégradations de la prospection locale :  python -m worker.local.monitor   (cron toutes les heures)

Détecte automatiquement : moteurs tous en cooldown ou tous à leur budget du jour, chute du taux de sites trouvés, SearXNG qui ne renvoie plus rien,
source indisponible, campagne bloquée. Envoie une alerte Telegram : une source cassée ne fonctionne jamais silencieusement pendant des jours.

Alerte au FRONT de chaque problème (première apparition) seulement : tant qu'un problème PERSISTE d'un passage à l'autre, il n'est pas re-signalé
(un budget de moteurs épuisé pendant 10 h ne doit pas produire 3 alertes identiques). Il redevient signalable s'il disparaît puis réapparaît.
"""
from __future__ import annotations

import json
import sys

from .. import alerts, db
from ..config import log
from . import health


def main() -> int:
    with db.connect() as conn:
        problems = health.degradations(conn)
        prev = db.fetch_one(conn, "SELECT problems FROM local_health_report WHERE id=1")
        prev_codes = {p["code"] for p in (json.loads(prev["problems"]) if prev and prev["problems"] else [])}
        db.execute(conn, """INSERT INTO local_health_report (id, at, problems) VALUES (1, UTC_TIMESTAMP(), %s)
                            ON DUPLICATE KEY UPDATE at=UTC_TIMESTAMP(), problems=VALUES(problems)""", (json.dumps(problems, ensure_ascii=False),))
        conn.commit()
    for p in problems:
        log.warning("[local-monitor] %s : %s", p["code"], p["message"])
        if p["code"] not in prev_codes:
            alerts.alert(f"local_{p['code']}", f"Prospection locale — {p['message']}")
    return 1 if any(p["level"] == "critical" for p in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
