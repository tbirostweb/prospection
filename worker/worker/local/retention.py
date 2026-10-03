"""Conservation RAISONNABLE des données de prospection (données publiques, nécessaires, sourcées… et pas gardées indéfiniment).

    python -m worker.local.retention            (cron quotidien, 04:30)

  * prospect jamais travaillé — ni contacté, ni ⭐, ni note, ni brouillon, ni réponse — découvert il y a plus de 18 mois : supprimé
    (s'il existe toujours, une campagne le retrouvera à jour) ;
  * prospect exclu ou écarté (chaîne, agence web, SCI, radiation…) : supprimé après 6 mois ;
  * cache d'API > 60 jours, journal d'erreurs > 180 jours, métriques de passage > 1 an.
JAMAIS supprimés : la liste « ne plus contacter » (`local_do_not_contact`), les sites signalés faux, tes retours, tes résultats
(`local_outcomes`, qui nourrissent l'apprentissage) et tout prospect que tu as travaillé. Durées réglables : LOCAL_RETENTION_MONTHS,
LOCAL_RETENTION_EXCLUDED_MONTHS.
"""
from __future__ import annotations

import os
import sys

from .. import db
from ..config import log

MONTHS = int(os.getenv("LOCAL_RETENTION_MONTHS", "18"))
EXCLUDED_MONTHS = int(os.getenv("LOCAL_RETENTION_EXCLUDED_MONTHS", "6"))
UNTOUCHED = """status IN ('DISCOVERED','ENRICHED','AUDITED','QUALIFIED') AND contacted_at IS NULL AND response_status IS NULL
               AND (notes IS NULL OR notes = '') AND draft_created_at IS NULL AND do_not_contact = 0"""


def purge(conn, months: int = MONTHS, excluded_months: int = EXCLUDED_MONTHS) -> dict:
    """Supprime ce qui n'a plus lieu d'être conservé. Renvoie le nombre de lignes supprimées par règle."""
    from . import learning
    learning.archive(conn)                                  # par sécurité : aucun résultat commercial n'est jamais perdu
    out = {}
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM local_prospects WHERE {UNTOUCHED} AND discovered_at < UTC_TIMESTAMP() - INTERVAL %s MONTH", (months,))
        out["prospects_untouched"] = cur.rowcount
        cur.execute(f"""DELETE FROM local_prospects WHERE {UNTOUCHED} AND (excluded_reason IS NOT NULL OR is_chain = 1 OR category = 'IGNORER')
                        AND discovered_at < UTC_TIMESTAMP() - INTERVAL %s MONTH""", (excluded_months,))
        out["prospects_excluded"] = cur.rowcount
        cur.execute("DELETE FROM local_http_cache WHERE fetched_at < UTC_TIMESTAMP() - INTERVAL 60 DAY")
        out["http_cache"] = cur.rowcount
        cur.execute("DELETE FROM local_events WHERE at < UTC_TIMESTAMP() - INTERVAL 180 DAY")
        out["events"] = cur.rowcount
        cur.execute("DELETE FROM local_run_metrics WHERE started_at < UTC_TIMESTAMP() - INTERVAL 1 YEAR")
        out["run_metrics"] = cur.rowcount
    conn.commit()
    return out


def main() -> int:
    with db.connect() as conn:
        out = purge(conn)
    log.info("[retention] %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
