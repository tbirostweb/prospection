"""Recalcule TOUS les scores locaux avec les réglages en vigueur (poids, seuils, poids d'activité) — sans réseau, en quelques secondes.
Lancé au démarrage du conteneur : après un changement de barème, l'application est à jour dès le redéploiement.   python -m worker.local.rescore"""
from __future__ import annotations

from .. import db
from ..config import log
from . import runner


def rescore_all(conn) -> int:
    n = 0
    for camp in db.fetch_all(conn, "SELECT * FROM local_campaigns"):
        ctx = runner.Ctx(conn, camp)
        for row in runner._campaign_prospects(conn, camp["id"]):
            runner._rescore(conn, row, ctx)
            n += 1
        conn.commit()
    return n


def main() -> None:
    with db.connect() as conn:
        try:
            from . import naflabels
            stats = naflabels.backfill(conn)                       # métier des fiches « Non classée » d'après leur code NAF, avant le recalcul
            if stats["examined"]:
                log.info("[local] métiers d'après le code NAF : %s", stats)
        except Exception as exc:  # noqa: BLE001 — n'empêche jamais le recalcul des scores
            conn.rollback()
            log.warning("[local] métiers d'après le code NAF en échec : %s", exc)
        log.info("[local] %d prospect(s) rescoré(s)", rescore_all(conn))


if __name__ == "__main__":
    main()
