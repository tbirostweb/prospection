"""Import de l'export OFFICIEL SIRENE (fichier `StockEtablissement_utf8.csv`, data.gouv.fr) pour une campagne — préférable à des milliers d'appels
d'API sur une grande zone (un département entier, plusieurs départements).

    python -m worker.local.bulk /chemin/StockEtablissement_utf8.csv --campaign 3 [--limit 5000]

Le fichier est lu EN FLUX (plusieurs Go : jamais chargé en mémoire) et filtré comme l'API : établissements ACTIFS et DIFFUSABLES seulement,
département (ou code postal) et activités de la campagne, chaînes / franchises / agences web / structures sans intérêt bannies, dédoublonnage par
SIRET. Le fichier n'a pas de coordonnées : l'établissement est situé au centre de SA COMMUNE (géolocalisation « commune », confiance faible) — la
distance sert à filtrer le rayon mais ne compte pas dans le score. Le worker enchaîne ensuite normalement (site, audit, contacts, score).
"""
from __future__ import annotations

import argparse
import json
import sys

from .. import db
from ..config import log
from . import http, naf as naf_mod, sirene, store
from .zone import haversine_km

COMMUNE_API = "https://geo.api.gouv.fr/communes/{code}"
COMMUNE_GEO_CONF = 0.4          # centre de la commune, pas l'adresse : jamais une distance « précise » dans le score


def commune_center(code: str, conn=None, cache: dict | None = None) -> tuple[float, float] | None:
    cache = cache if cache is not None else {}
    if code not in cache:
        try:
            data = http.get_json(COMMUNE_API.format(code=code), {"fields": "centre"}, conn=conn, cache_hours=24 * 30)
            lon, lat = ((data or {}).get("centre") or {}).get("coordinates") or (None, None)
            cache[code] = (float(lat), float(lon)) if lat is not None else None
        except http.ApiError:
            cache[code] = None
    return cache[code]


def import_for_campaign(conn, path: str, camp: dict, limit: int | None = None) -> dict:
    """Importe les établissements du fichier qui correspondent à la campagne. Renvoie les compteurs."""
    settings = store.load_settings(conn)
    catalog = naf_mod.load_catalog(settings.get("local_activities"))
    naf_mod.set_extra_brands(settings.get("local_banned_brands"))
    activities = json.loads(camp["activities"]) if isinstance(camp["activities"], str) else camp["activities"]
    codes, _unknown = naf_mod.resolve_activities(activities, catalog)
    if not codes:
        raise ValueError("campagne sans activité exploitable")
    dep = camp.get("department") or ((camp.get("postal_code") or "")[:2] or None)
    if not dep:
        raise ValueError("la campagne doit avoir un département ou un code postal (le fichier est filtré par département)")
    center = (float(camp["latitude"]), float(camp["longitude"])) if camp.get("latitude") is not None else None
    radius = float(camp["radius_km"]) if camp.get("radius_km") else None
    stats = {"read": 0, "banned": 0, "out_of_radius": 0, "imported": 0, "new": 0}
    centers: dict = {}
    for est in sirene.import_stock_csv(path, departments=[dep], naf_codes=codes, limit=limit):
        stats["read"] += 1
        if naf_mod.chain_signals(est.name, est.trade_name, est.company_size, est.employee_range, est.establishments_open, est.legal_category, est.naf_code) \
                or naf_mod.excluded_reason(est.naf_code, f"{est.trade_name or ''} {est.name}", legal_category=est.legal_category):
            stats["banned"] += 1
            continue
        pos = commune_center(est.commune_code, conn, centers) if est.commune_code else None
        if pos:
            est.latitude, est.longitude = pos
            if center:
                est.distance_km = round(haversine_km(center[0], center[1], *pos), 1)
                if radius and est.distance_km > radius + 3:            # centre de commune ≈ ± quelques km
                    stats["out_of_radius"] += 1
                    continue
        pid, created = store.upsert_establishment(conn, est, camp["id"], catalog)
        if pos:
            db.execute(conn, "UPDATE local_prospects SET geo_confidence=LEAST(COALESCE(geo_confidence, 1), %s) WHERE id=%s AND geo_confidence > %s",
                       (COMMUNE_GEO_CONF, pid, COMMUNE_GEO_CONF))
        stats["imported"] += 1
        stats["new"] += int(created)
        if stats["imported"] % 200 == 0:
            conn.commit()
    db.execute(conn, "UPDATE local_campaigns SET status='queued', enabled=1 WHERE id=%s", (camp["id"],))
    conn.commit()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Import de l'export SIRENE StockEtablissement pour une campagne")
    ap.add_argument("path")
    ap.add_argument("--campaign", type=int, required=True)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    with db.connect() as conn:
        camp = db.fetch_one(conn, "SELECT * FROM local_campaigns WHERE id=%s", (args.campaign,))
        if not camp:
            print(f"campagne #{args.campaign} introuvable")
            return 1
        stats = import_for_campaign(conn, args.path, camp, args.limit)
    log.info("[bulk] %s", stats)
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
