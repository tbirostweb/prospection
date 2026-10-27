"""Rattrapage des métiers : fiches sans métier (activity_label vide ou « Non classée ») mais AVEC un code NAF.

Le métier vient du catalogue (`naf.CATALOG` + réglages), sinon de l'intitulé officiel NAF (`data/naf_rev2.json`, repli sur la division). Les fiches
déjà classées ne sont jamais touchées. Idempotent et borné : chaque fiche examinée reçoit la provenance `activity` (code, version de la table,
date) ; une fiche dont le code reste inconnu n'est réexaminée que si la table change de version. Le score est recalculé (le poids d'activité en
dépend) — `python -m worker.local.naflabels` ; appelé aussi au démarrage (rescore) et à chaque passage de emailsearch.main."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .. import db
from ..config import log
from . import naf as naf_mod
from . import store

BATCH = 500
UNKNOWN_SQL = "(p.activity_label IS NULL OR TRIM(p.activity_label) = '' OR LOWER(TRIM(p.activity_label)) = 'non classée')"


def backfill(conn, limit: int = BATCH) -> dict:
    version = naf_mod.official()["version"]
    rows = db.fetch_all(conn, f"""SELECT p.id FROM local_prospects p WHERE {UNKNOWN_SQL} AND p.naf_code IS NOT NULL AND TRIM(p.naf_code) <> ''
        AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(p.provenance, '$.activity.version')), '') <> %s ORDER BY p.id LIMIT {int(limit)}""", (version,))
    stats = {"examined": len(rows), "labelled": 0, "unknown": 0}
    if not rows:
        return stats
    from . import runner
    catalogs: dict = {}
    settings = store.load_settings(conn)
    catalog = naf_mod.load_catalog(settings.get("local_activities"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in rows:
        row = db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (r["id"],))
        key, label, _web = naf_mod.activity_of(row["naf_code"], catalog)
        code = naf_mod.normalize_code(row["naf_code"])
        origin = "catalog" if key else (naf_mod.official_label(code)[1] or "unknown")
        if label:
            db.execute(conn, "UPDATE local_prospects SET activity_key=%s, activity_label=%s WHERE id=%s", (key, label, row["id"]))
            stats["labelled"] += 1
        else:
            stats["unknown"] += 1
        store.set_provenance(conn, row["id"], "activity", {"source": "naf_rev2", "code": code or row["naf_code"], "origin": origin, "version": version, "at": now})
        if label:                                                     # le poids d'activité entre dans le score : recalcul avec la campagne de la fiche
            camp_id = db.fetch_one(conn, "SELECT campaign_id FROM local_prospect_campaigns WHERE prospect_id=%s ORDER BY campaign_id LIMIT 1", (row["id"],))
            if camp_id:
                if camp_id["campaign_id"] not in catalogs:
                    camp = db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (camp_id["campaign_id"],))
                    catalogs[camp_id["campaign_id"]] = runner.Ctx(conn, camp) if camp else None
                ctx = catalogs[camp_id["campaign_id"]]
                if ctx:
                    runner._rescore(conn, db.fetch_one(conn, "SELECT * FROM local_prospects WHERE id=%s", (row["id"],)), ctx)
        conn.commit()
    return stats


def main() -> None:
    with db.connect() as conn:
        log.info("[local] métiers d'après le code NAF : %s", json.dumps(backfill(conn)))


if __name__ == "__main__":
    main()
