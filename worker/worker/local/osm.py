"""OpenStreetMap comme ENRICHISSEMENT (site, téléphone, e-mail publics) — jamais comme vérité.

Usage responsable des services publics OSM :
  * UNE seule requête Overpass par campagne (zone + rayon), jamais par entreprise ; réponse mise en cache 7 jours (`local_http_cache`) ;
  * seulement les objets qui portent déjà un site, un téléphone ou un e-mail (filtre côté serveur : réponse légère) ;
  * Nominatim (public) n'est JAMAIS utilisé : le géocodage vient de geo.api.gouv.fr ;
  * `LOCAL_OSM_URL` permet de pointer une instance Overpass dédiée ; `LOCAL_OSM=0` désactive tout.
Une correspondance exige la PROXIMITÉ (≤ 150 m) ET un nom compatible ; elle reste une donnée « source OSM » de confiance moyenne : un site
issu d'OSM est ensuite VÉRIFIÉ comme n'importe quel candidat (`sitefinder`), un téléphone OSM est signalé comme tel (contactEvidence).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

import httpx

from .. import db
from ..config import USER_AGENT, log
from . import errors
from .zone import haversine_km
from .textmatch import distinctive_tokens, fold, name_tokens, token_fraction

OVERPASS_URL = os.getenv("LOCAL_OSM_URL", "https://overpass-api.de/api/interpreter")
ENABLED = os.getenv("LOCAL_OSM", "1") != "0"
SITE_HINT = os.getenv("LOCAL_OSM_SITE_HINT", "1") != "0"      # 0 : le site OSM n'est PAS proposé au résolveur (sert de référence indépendante pour mesurer le rappel)
CACHE_DAYS = 7
MAX_ELEMENTS = 5000              # plafond de la réponse Overpass : atteint = zone dense, couverture OSM PARTIELLE (signalée, jamais silencieuse)
MATCH_RADIUS_M = 120
MIN_NAME_MATCH = 0.75            # constaté en réel : à 0,5 des noms courts / prénoms rapprochaient des lieux sans rapport
_transport: httpx.BaseTransport | None = None      # tests


@dataclass
class OsmPlace:
    osm_id: str
    name: str
    lat: float
    lon: float
    website: str | None = None
    phone: str | None = None
    email: str | None = None
    kind: str | None = None
    opening_hours: str | None = None


def build_query(lat: float, lon: float, radius_m: int) -> str:
    around = f"(around:{radius_m},{lat:.5f},{lon:.5f})"
    tags = ("website", "contact:website", "phone", "contact:phone", "email", "contact:email")
    body = "".join(f'nwr["name"]["{t}"]{around};' for t in tags)
    return f"[out:json][timeout:90];({body});out center tags {MAX_ELEMENTS};"


def fetch_places(lat: float, lon: float, radius_km: float, conn=None) -> list[OsmPlace]:
    """Objets OSM nommés avec coordonnées de contact, dans le rayon. Lève `SOURCE_UNAVAILABLE` (jamais « aucun lieu ») en cas d'échec."""
    radius_m = int(min(radius_km, 30) * 1000)
    query = build_query(lat, lon, radius_m)
    key = hashlib.sha1(("osm|" + OVERPASS_URL + query).encode()).hexdigest()
    data = None
    if conn is not None:
        row = db.fetch_one(conn, "SELECT body FROM local_http_cache WHERE cache_key=%s AND fetched_at > UTC_TIMESTAMP() - INTERVAL %s DAY", (key, CACHE_DAYS))
        data = json.loads(row["body"]) if row else None
    if data is None:
        try:
            with httpx.Client(timeout=120, transport=_transport) as c:
                r = c.post(OVERPASS_URL, data={"data": query}, headers={"User-Agent": USER_AGENT})
            if r.status_code != 200:
                raise errors.LocalError(errors.SOURCE_UNAVAILABLE, f"Overpass HTTP {r.status_code}")
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise errors.LocalError(errors.SOURCE_UNAVAILABLE, f"Overpass : {exc}") from exc
        if conn is not None:
            db.execute(conn, """INSERT INTO local_http_cache (cache_key, body) VALUES (%s,%s)
                                ON DUPLICATE KEY UPDATE body=VALUES(body), fetched_at=UTC_TIMESTAMP()""", (key, json.dumps(data, ensure_ascii=False)))
            conn.commit()
    return parse(data)


def parse(data: dict) -> list[OsmPlace]:
    out: list[OsmPlace] = []
    for el in (data or {}).get("elements") or []:
        tags = el.get("tags") or {}
        c = el if "lat" in el else el.get("center") or {}
        if not tags.get("name") or "lat" not in c:
            continue
        out.append(OsmPlace(f"{el.get('type', 'n')[0]}{el.get('id')}", tags["name"], float(c["lat"]), float(c["lon"]),
                            tags.get("website") or tags.get("contact:website"), tags.get("phone") or tags.get("contact:phone"),
                            tags.get("email") or tags.get("contact:email"), tags.get("shop") or tags.get("amenity") or tags.get("craft") or tags.get("office"),
                            (tags.get("opening_hours") or "")[:200] or None))
    return out


def match(company: dict, places: list[OsmPlace]) -> dict | None:
    """Meilleure correspondance OSM (proximité ≤ 120 m ET nom compatible à ≥ 75 %) ou None. Ne renvoie jamais une correspondance sur la proximité seule."""
    lat, lon = company.get("latitude"), company.get("longitude")
    if lat is None or lon is None:
        return None
    lat, lon = float(lat), float(lon)
    names = [n for n in (company.get("trade_name"), company.get("company_name")) if n]
    best = None
    for pl in places:
        d = haversine_km(lat, lon, pl.lat, pl.lon) * 1000
        if d > MATCH_RADIUS_M:
            continue
        sim = 0.0
        for n in names:
            dt = distinctive_tokens(n) or name_tokens(n)
            sim = max(sim, token_fraction(dt, pl.name), 1.0 if fold(n) == fold(pl.name) else 0.0)
        if sim < MIN_NAME_MATCH:
            continue
        score = sim - d / 1000
        if best is None or score > best[0]:
            best = (score, pl, d, sim)
    if not best:
        return None
    _s, pl, d, sim = best
    return {"osm_id": pl.osm_id, "name": pl.name, "distance_m": int(d), "name_match": round(sim, 2), "website": pl.website, "phone": pl.phone,
            "email": pl.email, "kind": pl.kind, "opening_hours": pl.opening_hours, "confidence": round(min(0.8, 0.4 + 0.4 * sim - d / 1000), 2)}


BRAND_RADIUS_M = 12        # même adresse (même bâtiment, même numéro) : au-delà, ce n'est qu'un voisin


def brand_at_address(company: dict, places: list[OsmPlace]) -> dict | None:
    """Enseigne de chaîne (liste bannie) du MÊME MÉTIER à la même adresse que l'établissement : {'brand','evidence'} ou None.
    Sert à reconnaître un franchisé qui s'appelle autrement que son enseigne. Un voisin d'un autre métier ne compte jamais."""
    from .naf import brand_of
    from .franchises import sector_matches
    lat, lon = company.get("latitude"), company.get("longitude")
    if lat is None or lon is None:
        return None
    for pl in places:
        d = haversine_km(float(lat), float(lon), pl.lat, pl.lon) * 1000
        if d > BRAND_RADIUS_M:
            continue
        b = brand_of(pl.name, company.get("naf_code"))
        if b and sector_matches(b, company.get("naf_code")):
            return {"brand": b, "evidence": f"à {int(d)} m d'un établissement « {pl.name} » (OpenStreetMap), enseigne de chaîne du même métier : franchisé probable"}
    return None
