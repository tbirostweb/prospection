"""Rapport de CALIBRATION du score (lecture seule) :  python -m worker.local.calibration

Compare le score aux décisions réelles de l'utilisateur : parmi les prospects notés 80+, 60-79, 45-59… combien ont été jugés bons (⭐ « À contacter »,
réponse reçue, client) ou mauvais (🚫 « Perdu / pas adapté ») ? Regroupe aussi les raisons du 🚫 (mauvais site, chaîne, site finalement bon…).
NE MODIFIE JAMAIS les poids ni les seuils : tant que l'échantillon est trop petit, le rapport le dit et s'abstient de toute recommandation.
"""
from __future__ import annotations

import json
import sys

from .. import db

MIN_JUDGED_PER_BAND = 20
BANDS = [(80, 101, "80+"), (60, 80, "60-79"), (45, 60, "45-59"), (0, 45, "< 45")]
GOOD = ("TO_CONTACT", "CONTACTED", "REPLIED", "INTERESTED", "WON")


def report(conn) -> dict:
    out = {"bands": [], "reasons": [], "recommendation": None}
    judged_total = 0
    for lo, hi, label in BANDS:
        r = db.fetch_one(conn, """SELECT COUNT(*) AS n, SUM(status IN ('TO_CONTACT','CONTACTED','REPLIED','INTERESTED','WON')) AS good,
                                         SUM(status='LOST' OR response_status='NOT_A_FIT') AS bad, SUM(status IN ('REPLIED','INTERESTED','WON')) AS replies,
                                         SUM(status='WON') AS won
                                  FROM local_prospects WHERE score_stage='FINAL' AND prospect_score >= %s AND prospect_score < %s""", (lo, hi)) or {}
        n, good, bad = int(r.get("n") or 0), int(r.get("good") or 0), int(r.get("bad") or 0)
        judged = good + bad
        judged_total += judged
        out["bands"].append({"band": label, "prospects": n, "judged": judged, "good": good, "bad": bad, "replies": int(r.get("replies") or 0), "won": int(r.get("won") or 0),
                             "good_rate": round(good / judged, 3) if judged >= MIN_JUDGED_PER_BAND else None,
                             "note": None if judged >= MIN_JUDGED_PER_BAND else f"trop peu de décisions ({judged} < {MIN_JUDGED_PER_BAND}) : taux non affiché"})
    out["reasons"] = db.fetch_all(conn, "SELECT feedback_reason AS reason, COUNT(*) AS n FROM local_prospects WHERE feedback_reason IS NOT NULL GROUP BY feedback_reason ORDER BY n DESC")
    ok = [b for b in out["bands"] if b["good_rate"] is not None]
    out["recommendation"] = ("Échantillon insuffisant : ne modifier AUCUN poids. Continuer à marquer ⭐ / 🚫 (avec la raison)." if len(ok) < 2
                             else "Comparer les taux par tranche : un score qui ne sépare pas les tranches justifie une revue MANUELLE des règles (jamais un ajustement automatique).")
    out["judged_total"] = judged_total
    return out


def main() -> int:
    with db.connect() as conn:
        conn.autocommit = True
        print(json.dumps(report(conn), ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
