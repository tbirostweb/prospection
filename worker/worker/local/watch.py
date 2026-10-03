"""Veille des NOUVELLES entreprises d'une zone :  python -m worker.local.watch   (cron quotidien ; chaque campagne à son rythme)

Pour chaque campagne où la veille est activée (tous les 7 ou 30 jours) :
  1. BODACC : créations publiées depuis la dernière veille dans les départements de la zone (celui de la campagne + ceux déjà vus parmi ses prospects) ;
  2. SIRENE : pour chaque SIREN inconnu, l'établissement (adresse, coordonnées, activité, état) ;
  3. gardé seulement s'il est actif, diffusable, dans le rayon et dans les activités de la campagne ; ajouté comme prospect marqué « nouvelle entreprise » ;
  4. la campagne est relancée : le site, les contacts et le score sont calculés comme pour tout prospect ;
  5. une alerte Telegram (`notify.notify_new_businesses`) les annonce une fois évalués.
Une création n'est qu'un SIGNAL : jamais une preuve de besoin, ni un contact automatique.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone

from .. import db
from ..config import log
from . import bodacc, errors, naf as naf_mod, sirene, store

FIRST_LOOKBACK_DAYS = 35
MAX_LOOKUPS = 250                 # SIREN interrogés au plus par campagne et par veille (≈ 90 s à la cadence de l'API)


def due_campaigns(conn) -> list[dict]:
    return db.fetch_all(conn, """SELECT * FROM local_campaigns WHERE enabled=1 AND watch=1 AND latitude IS NOT NULL
                                 AND (last_watch_at IS NULL OR last_watch_at <= UTC_TIMESTAMP() - INTERVAL watch_every_days DAY) ORDER BY id""")


def _departments(conn, camp: dict) -> list[str]:
    deps = {camp.get("department")} | {r["department"] for r in db.fetch_all(conn, """SELECT DISTINCT p.department FROM local_prospects p
                                        JOIN local_prospect_campaigns c ON c.prospect_id=p.id WHERE c.campaign_id=%s""", (camp["id"],))}
    return sorted(d for d in deps if d)


def watch_campaign(conn, camp: dict) -> dict:
    settings = store.load_settings(conn)
    catalog = naf_mod.load_catalog(settings.get("local_activities"))
    naf_mod.set_extra_brands(settings.get("local_banned_brands"))
    acts = json.loads(camp["activities"]) if isinstance(camp["activities"], (str, bytes)) else camp["activities"]
    codes, _unknown = naf_mod.resolve_activities(acts, catalog)
    last = camp.get("last_watch_at")
    since = (last.date() if isinstance(last, datetime) else date.today() - timedelta(days=FIRST_LOOKBACK_DAYS)) - timedelta(days=2)   # recouvrement : rien ne passe entre deux veilles
    stats = {"departments": [], "creations": 0, "looked_up": 0, "new": 0}
    lat, lon, radius = float(camp["latitude"]), float(camp["longitude"]), float(camp["radius_km"])
    for dep in _departments(conn, camp):
        creations = bodacc.fetch_creations(dep, since=since, conn=conn)
        stats["departments"].append(dep)
        stats["creations"] += len(creations)
        known = {r["siren"] for r in db.fetch_all(conn, "SELECT siren FROM local_prospects WHERE siren IN (%s)" % ",".join(["%s"] * len(creations)),
                                                  tuple(creations))} if creations else set()
        for siren, sig in list(creations.items()):
            if siren in known or stats["looked_up"] >= MAX_LOOKUPS:
                continue
            stats["looked_up"] += 1
            unit = sirene.lookup_siren(siren, conn)
            if not unit or unit.get("statut_diffusion") == "P" or unit.get("etat_administratif") == "C":
                continue                                        # non diffusable ou déjà cessée : jamais prospectée
            for est in sirene.parse_unit(unit, (lat, lon), radius, codes):
                if est.distance_km is None:
                    continue                                    # sans coordonnées, impossible de vérifier qu'elle est dans le rayon
                if store.is_do_not_contact(conn, est.siret, est.siren):
                    continue
                if naf_mod.chain_signals(est.name, est.trade_name, est.company_size, est.employee_range, est.establishments_open, est.legal_category,
                                     est.naf_code) or naf_mod.excluded_reason(est.naf_code, f"{est.trade_name or ''} {est.name}", legal_category=est.legal_category):
                    stats["banned"] = stats.get("banned", 0) + 1
                    continue                                    # franchise, chaîne ou agence web : bannie, jamais annoncée
                pid, created = store.upsert_establishment(conn, est, camp["id"], catalog)
                if created:
                    db.execute(conn, "UPDATE local_prospects SET new_business_at=UTC_TIMESTAMP(), bodacc=%s WHERE id=%s", (json.dumps(sig), pid))
                    store.add_source(conn, pid, "bodacc", sig.get("published"))
                    stats["new"] += 1
        conn.commit()
    db.execute(conn, """UPDATE local_campaigns SET last_watch_at=UTC_TIMESTAMP(),
                        status=IF(%s > 0 AND status IN ('done','idle','error'), 'queued', status) WHERE id=%s""", (stats["new"], camp["id"]))
    conn.commit()
    return stats


def run() -> list[dict]:
    out = []
    with db.connect() as conn:
        for camp in due_campaigns(conn):
            t0 = time.monotonic()
            try:
                st = watch_campaign(conn, camp)
            except Exception as exc:  # noqa: BLE001 — une veille en échec (BODACC, SIRENE) est retentée au prochain passage, sans bloquer les autres
                conn.rollback()
                errors.record(conn, errors.classify(exc), "watch", campaign_id=camp["id"], detail=str(exc))
                conn.commit()
                log.warning("[local-watch] campagne #%s : %s", camp["id"], exc)
                continue
            log.info("[local-watch] « %s » : %s (%.0f s)", camp["name"], st, time.monotonic() - t0)
            out.append({"campaign": camp["id"], **st})
    return out


if __name__ == "__main__":
    run()
