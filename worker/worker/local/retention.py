"""Conservation RAISONNABLE des données de prospection (données publiques, nécessaires, sourcées… et pas gardées indéfiniment).

    python -m worker.local.retention            (cron quotidien, 04:30)

  * prospect jamais travaillé — ni contacté, ni ⭐, ni note, ni brouillon, ni réponse — découvert il y a plus de 18 mois : supprimé
    (s'il existe toujours, une campagne le retrouvera à jour) ;
  * prospect exclu ou écarté (chaîne, agence web, SCI, radiation…) : supprimé après 6 mois ;
  * prospect TRAVAILLÉ (contacté, noté, brouillon, écarté, « ne plus contacter »…) sans aucune activité depuis 36 mois : supprimé
    (collecte ou dernier contact entrant enregistré, jamais une relance interne) — SAUF les prospects « Gagné » (relation client :
    durée à fixer par l'éditeur, voir docs/SECURITE.md) ;
  * résultats d'apprentissage (`local_outcomes`) dont le prospect n'existe plus et datant de plus de 36 mois : ANONYMISÉS
    (identifiant SIRET remplacé par une clé aléatoire ; seules restent activité, ville, signaux et issue, pour les statistiques) ;
  * retours « mauvais site / site trouvé » (instantané de l'entreprise) de plus de 36 mois dont le prospect n'existe plus : supprimés ;
  * cache d'API > 60 jours, journal d'erreurs > 180 jours, métriques de passage > 1 an.
JAMAIS supprimés : la liste « ne plus contacter » (`local_do_not_contact` : données minimales nécessaires pour respecter
l'opposition) et les sites signalés faux (`local_bad_sites` : SIRET + domaine). Durées réglables : LOCAL_RETENTION_MONTHS,
LOCAL_RETENTION_EXCLUDED_MONTHS, LOCAL_RETENTION_WORKED_MONTHS.
"""
from __future__ import annotations

import os
import sys

from .. import db
from ..config import log

MONTHS = int(os.getenv("LOCAL_RETENTION_MONTHS") or "18")
EXCLUDED_MONTHS = int(os.getenv("LOCAL_RETENTION_EXCLUDED_MONTHS") or "6")
WORKED_MONTHS = int(os.getenv("LOCAL_RETENTION_WORKED_MONTHS") or "36")
# Dernière activité connue d'un prospect (chaque date absente est remplacée par la découverte).
# Une relance ou une note interne ne redémarre jamais la durée de prospection.
LAST_ACTIVITY = "GREATEST(discovered_at, COALESCE(last_prospect_contact_at, discovered_at))"
UNTOUCHED = """status IN ('DISCOVERED','ENRICHED','AUDITED','QUALIFIED') AND contacted_at IS NULL AND response_status IS NULL
               AND (notes IS NULL OR notes = '') AND draft_created_at IS NULL AND do_not_contact = 0"""


def purge(conn, months: int = MONTHS, excluded_months: int = EXCLUDED_MONTHS, worked_months: int = WORKED_MONTHS) -> dict:
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
        cur.execute(f"""DELETE FROM local_prospects WHERE (status IS NULL OR status <> 'WON')
                        AND {LAST_ACTIVITY} < UTC_TIMESTAMP() - INTERVAL %s MONTH""", (worked_months,))
        out["prospects_worked"] = cur.rowcount
        cur.execute("""UPDATE local_outcomes o SET o.okey = CONCAT('a:', REPLACE(UUID(), '-', ''))
                       WHERE o.okey NOT LIKE 'a:%%'
                         AND COALESCE(o.contacted_at, o.updated_at) < UTC_TIMESTAMP() - INTERVAL %s MONTH
                         AND NOT EXISTS (SELECT 1 FROM local_prospects p WHERE p.siret = o.okey OR CONCAT('id:', p.id) = o.okey)""",
                    (worked_months,))
        out["outcomes_anonymized"] = cur.rowcount
        cur.execute("""DELETE f FROM local_site_feedback f LEFT JOIN local_prospects p ON p.id = f.prospect_id
                       WHERE p.id IS NULL AND f.created_at < UTC_TIMESTAMP() - INTERVAL %s MONTH""", (worked_months,))
        out["site_feedback"] = cur.rowcount
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
